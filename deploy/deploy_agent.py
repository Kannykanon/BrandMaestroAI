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


REQUIRED_ENV = [
    "PARALLEL_API_KEY", "GOOGLE_API_KEY", "POSTGRES_URI", "REDIS_URL",
]


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
            "langgraph>=0.0.51",
            "langchain-google-genai>=1.0.3,<1.1.0",
            "parallel-web>=1.3.0,<2.0.0",
            "psycopg2-binary",
            "redis",
            "llama-index",
            "llama-index-vector-stores-postgres",
            "llama-index-embeddings-fastembed",
            "fastembed",
        ],
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
