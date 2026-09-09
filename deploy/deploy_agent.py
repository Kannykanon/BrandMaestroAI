# deploy/deploy_agent.py
"""
One-time (or one-per-release) deploy of BrandMaestro's LangGraph pipeline to
Google Cloud Agent Platform Runtime.

Run this from your machine (not inside the Docker Compose stack) after:
  1. `gcloud auth application-default login`
  2. Enabling the Agent Platform API + billing on your GCP project
  3. `pip install --upgrade google-cloud-aiplatform[agent_engines,langgraph]`
  4. Exporting: PROJECT_ID, LOCATION (e.g. us-central1), STAGING_BUCKET
     (a GCS bucket URI, e.g. gs://your-bucket), and PARALLEL_API_KEY /
     GOOGLE_API_KEY / your DB + Redis + RabbitMQ connection strings — the
     deployed agent needs the same env vars your Celery workers need,
     since the nodes still call Postgres/Redis for RAG + Brand Brain.

Usage:
    python deploy/deploy_agent.py

This prints the deployed resource name
(projects/PROJECT_ID/locations/LOCATION/reasoningEngines/RESOURCE_ID) —
save it. `main.py` / `celery_task.py` will need it (via
AGENT_ENGINE_RESOURCE_NAME) to call the deployed agent instead of building
the graph in-process.
"""
import os
import sys

import vertexai
from vertexai import agent_engines

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from deploy.agent_runtime import get_agent_engine_app  # noqa: E402


# GOOGLE_API_KEY only belongs here on the ai_studio path. On vertex_ai the
# agent authenticates with Application Default Credentials and no key
# exists, so demanding it unconditionally refused to deploy the exact
# configuration that actually runs in production.
REQUIRED_ENV = ["PARALLEL_API_KEY", "POSTGRES_URI", "REDIS_URL"]
if os.getenv("LLM_PROVIDER", "ai_studio").strip().lower() != "vertex_ai":
    REQUIRED_ENV.append("GOOGLE_API_KEY")


def main():
    missing = [v for v in REQUIRED_ENV if not os.getenv(v)]
    if missing:
        raise SystemExit(
            f"Missing required env vars before deploying: {', '.join(missing)}"
        )

    project = os.environ["PROJECT_ID"]
    location = os.getenv("LOCATION", "us-central1")
    staging_bucket = os.environ["STAGING_BUCKET"]

    vertexai.init(project=project, location=location, staging_bucket=staging_bucket)

    app = get_agent_engine_app()

    remote_app = agent_engines.create(
        app,
        requirements=[
            "google-cloud-aiplatform[agent_engines,langgraph]==1.125.0",
            "langchain",
            "langchain-text-splitters",
            "langchain-google-vertexai>=2.0.0",
            "structlog",
            "python-dotenv",
            "sqlalchemy",
            "tenacity",
            "langgraph>=0.0.51",
            "langchain-google-genai>=2.0.0",
            "parallel-web>=1.3.0,<2.0.0",
            "psycopg2-binary",
            "redis",
            "llama-index",
            "llama-index-vector-stores-postgres",
            "llama-index-embeddings-fastembed",
            "fastembed",
        ],
        env_vars={var: os.environ[var] for var in REQUIRED_ENV} | {"LLM_PROVIDER": os.getenv("LLM_PROVIDER", "ai_studio")},

        extra_packages=["deploy", "graph", "nodes", "utils", "prompts", "database.py", "model.py", "brand_rag.py", "learning_memory.py", "brand_metrics.py", "search.py", "embedding_stategy.py", "chunking_stategy.py"],
        display_name="brandmaestro-content-pipeline",
        description=(
            "BrandMaestro AI: Researcher -> Writer -> Enforcer -> Deployer "
            "brand-voice content generation pipeline."
        ),
    )

    print("Deployed. Resource name:")
    print(remote_app.resource_name)
    print(
        "\nSet this as AGENT_ENGINE_RESOURCE_NAME in your API/worker "
        "environment to route generation requests through Agent Platform "
        "Runtime instead of the in-process graph."
    )


if __name__ == "__main__":
    main()
