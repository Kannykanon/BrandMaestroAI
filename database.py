from contextlib import contextmanager

import os
from datetime import datetime, timedelta
from contextlib import contextmanager
from typing import Optional

import structlog
from dotenv import load_dotenv
from sqlalchemy import create_engine, MetaData, String, Integer, Boolean, DateTime, Text, DECIMAL, ForeignKey, CheckConstraint, UniqueConstraint, func, event
from sqlalchemy.orm import Mapped, mapped_column, sessionmaker, Session, DeclarativeBase, relationship
from sqlalchemy.pool import QueuePool
from uuid import uuid4
from sqlalchemy.dialects.postgresql import JSONB, INET
from sqlalchemy.exc import OperationalError, DatabaseError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from sqlalchemy import Float, Column

# Load environment variables
load_dotenv()

logger = structlog.get_logger(__name__)

# ===== BASE MODEL =====
class Model(DeclarativeBase):
    metadata = MetaData(naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })


# ===== MODELS =====
class User(Model):
    __tablename__ = 'users'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(100), unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    business_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True, default=lambda: str(uuid4()))
    business_name: Mapped[Optional[str]] = mapped_column(String(255))
    industry: Mapped[Optional[str]] = mapped_column(String(100))
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )
    
    is_active: Mapped[bool] = mapped_column(Boolean, server_default='true', nullable=False)
    subscription_tier: Mapped[str] = mapped_column(String(50), server_default='free', nullable=False)
    
    # password_hash removed
    daily_generation_limit: Mapped[int] = mapped_column(Integer, server_default='50', nullable=False)
    monthly_generations_used: Mapped[int] = mapped_column(Integer, server_default='0', nullable=False)
    
    settings: Mapped[dict] = mapped_column(JSONB, server_default='{}', nullable=False)
    
    # Relationships
    documents = relationship("BrandDocument", back_populates="user", cascade="all, delete-orphan")
    generations = relationship("ReviewerLearning", back_populates="user", cascade="all, delete-orphan")
    feedbacks = relationship("GenerationFeedback", back_populates="user", cascade="all, delete-orphan")
    api_keys = relationship("APIKey", back_populates="user", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f'User(id={self.id}, business_id={self.business_id}, email={self.email})'


# What an uploaded document is FOR. These are two different jobs and the same
# file is almost never right for both:
#   "voice"     - previously successful content. Teaches the Brand Brain how the
#                 brand writes (voice, tone, style). Extracted via long context.
#   "reference" - a product document, press kit, brief or spec. Supplies FACTS
#                 about the subject through the RAG pipeline. It must NOT teach
#                 the brand its voice: a press kit is written in press-kit
#                 register, and letting it into the Brand Brain drags generated
#                 copy toward that register instead of the brand's own.
DOC_ROLE_VOICE = "voice"
DOC_ROLE_REFERENCE = "reference"
DOC_ROLES = (DOC_ROLE_VOICE, DOC_ROLE_REFERENCE)


class BrandDocument(Model):
    __tablename__ = 'brand_documents'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    business_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # See DOC_ROLE_* above. Defaults to "voice" so existing rows keep their
    # current behaviour; only documents explicitly marked "reference" are held
    # out of Brand Brain extraction.
    doc_role: Mapped[str] = mapped_column(
        String(20), server_default=DOC_ROLE_VOICE, nullable=False, index=True
    )
    file_path: Mapped[Optional[str]] = mapped_column(Text)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    file_content: Mapped[Optional[str]] = mapped_column(Text)  # Store actual file content
    
    status: Mapped[str] = mapped_column(String(50), server_default='pending', nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    
    vector_table_name: Mapped[Optional[str]] = mapped_column(String(255))
    document_count: Mapped[int] = mapped_column(Integer, server_default='1', nullable=False)
    chunk_count: Mapped[Optional[int]] = mapped_column(Integer)
    
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    user = relationship("User", back_populates="documents")
    
    metrics: Mapped[Optional[str]] = mapped_column(Text)
    doc_hashes: Mapped[Optional[list]] = mapped_column(JSONB)
    
    def __repr__(self):
        return f'BrandDocument(id={self.id}, filename={self.filename}, type={self.content_type})'


class BrandMetrics(Model):
    __tablename__ = 'brand_metrics'

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    content_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)   
    total_pages: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  
    page_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  
    # FK to the source document. NULL for rows sourced from high-scoring generations.
    doc_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey('brand_documents.id', ondelete='CASCADE'),
        nullable=True,
        index=True
    )
    doc_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Structured extraction result from LLM — maps directly to METRICS_EXTRACTION schema
    extracted: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default='{}')
    # Higher weight for human-approved generation feedback rows
    score_weight: Mapped[float] = mapped_column(Float, nullable=False, server_default='1.0')
    # Source tells us whether this row came from an uploaded doc or a generation feedback loop
    source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default='document'  # 'document' | 'generation'
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    document = relationship('BrandDocument', backref='metrics_rows')

    __table_args__ = (
        # Prevents the same document being extracted twice for the same business + content_type
        UniqueConstraint('business_id', 'content_type', 'doc_hash', name='uq_brand_metrics_hash'),
    )

    def __repr__(self):
        return f'BrandMetrics(business_id={self.business_id}, content_type={self.content_type}, source={self.source})'


class BrandBrain(Model):
    __tablename__ = "brand_brains"
    
    id = Column(Integer, primary_key=True)
    business_id = Column(String, nullable=False)
    content_type = Column(String, nullable=False)
    synthesis_text = Column(Text, nullable=False)  # the brain
    profile_count = Column(Integer, default=0)      # rows baked in
    last_synthesis_at = Column(DateTime, default=datetime.utcnow)
    version = Column(Integer, default=1)            # for tracking drift
    
    __table_args__ = (
        UniqueConstraint('business_id', 'content_type', name='uq_brand_brains'),
    )

class Generation(Model):
    __tablename__ = "generations"

    generation_id:Mapped[str] = mapped_column(String, primary_key=True)
    business_id:Mapped[str]   = mapped_column(String, nullable=False, index=True)
    content_type:Mapped[str]  = mapped_column(String, nullable=False)
    topic:Mapped[str]       = mapped_column(String, nullable=False)
    format_type:Mapped[str]   = mapped_column(String, nullable=False)
    user_id:Mapped[int]       = mapped_column(Integer, nullable=True)
    status:Mapped[str]        = mapped_column(String, default="pending") 
    score:Mapped[float]         = mapped_column(Float, nullable=True)
    content:Mapped[str]       = mapped_column(Text, nullable=True)        
    created_at:Mapped[datetime]    = mapped_column(DateTime, default=datetime.utcnow)
    completed_at:Mapped[datetime]  = mapped_column(DateTime, nullable=True)

class GenerationFeedback(Model):
    __tablename__ = 'generation_feedback'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    business_id:Mapped[str]   = mapped_column(String, nullable=False, index=True)
    generation_id: Mapped[int] = mapped_column(ForeignKey('reviewer_learning.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    
    feedback_type: Mapped[str] = mapped_column(String(50), nullable=False)
    
    rating: Mapped[Optional[int]] = mapped_column(Integer)
    comment: Mapped[Optional[str]] = mapped_column(Text)
    
    issues: Mapped[Optional[dict]] = mapped_column(JSONB)

    used_for_retraining: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    style_match: Mapped[float] = mapped_column(Float, default=0.0)
    tone_match: Mapped[float] = mapped_column(Float, default=0.0)
    structure_match: Mapped[float] = mapped_column(Float, default=0.0)
    signature_match: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False
        )
    
    generation = relationship("ReviewerLearning", back_populates="feedbacks")
    user = relationship("User", back_populates="feedbacks")
    
    __table_args__ = (
        CheckConstraint('rating >= 1 AND rating <= 5', name='valid_rating'),
    )
    
    def __repr__(self):
        return f'GenerationFeedback(id={self.id}, type={self.feedback_type}, rating={self.rating})'


class UsageAnalytics(Model):
    __tablename__ = 'usage_analytics'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    business_id: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    event_data: Mapped[Optional[dict]] = mapped_column(JSONB)
    
    session_id: Mapped[Optional[str]] = mapped_column(String(255))
    ip_address: Mapped[Optional[str]] = mapped_column(INET)
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    
    def __repr__(self):
        return f'UsageAnalytics(id={self.id}, event={self.event_type})'


class APIKey(Model):
    __tablename__ = 'api_keys'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    
    key_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    key_prefix: Mapped[Optional[str]] = mapped_column(String(20))
    name: Mapped[Optional[str]] = mapped_column(String(100))
    
    scopes: Mapped[dict] = mapped_column(JSONB, server_default='["read", "write"]', nullable=False)
    
    is_active: Mapped[bool] = mapped_column(Boolean, server_default='true', nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, server_default='60', nullable=False)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    user = relationship("User", back_populates="api_keys")
    
    def __repr__(self):
        return f'APIKey(id={self.id}, prefix={self.key_prefix}, active={self.is_active})'


class ReviewerLearning(Model):
    __tablename__ = 'reviewer_learning'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    generation_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    business_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=True, index=True)
    
    # Generation metadata 
    topic: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(50), index=True)
    format_type: Mapped[str] = mapped_column(String(100))
    generated_content: Mapped[str] = mapped_column(Text)
    creative_angle: Mapped[str] = mapped_column(Text)
    
    # Agent scoring
    agent_auto_score: Mapped[float] = mapped_column(DECIMAL(3, 1))
    agent_confidence: Mapped[Optional[float]] = mapped_column(DECIMAL(3, 2))
    agent_auto_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Human feedback
    has_human_feedback: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    human_approved: Mapped[Optional[bool]] = mapped_column(Boolean)
    human_score: Mapped[Optional[float]] = mapped_column(DECIMAL(3, 1))
    human_feedback: Mapped[Optional[str]] = mapped_column(Text)
    
    # Learning signals
    agent_correct: Mapped[Optional[bool]] = mapped_column(Boolean)
    features_used: Mapped[Optional[dict]] = mapped_column(JSONB)
    used_for_retraining: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Regeneration provenance. `use_search` records the research mode the
    # original request ran with so a rejection can be re-run the same way
    # instead of silently switching sources; `regeneration_depth` counts how
    # many reject -> regenerate hops produced this generation, so the human
    # loop can be capped the way the writer/enforcer loop already is.
    use_search: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    regeneration_depth: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    user = relationship("User", back_populates="generations")
    feedbacks = relationship("GenerationFeedback", back_populates="generation", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f'ReviewerLearning(generation_id={self.generation_id}, business={self.business_id})'


# ===== DATABASE ENGINE CONFIGURATION =====
def get_database_url() -> str:
    # Use DATABASE_URL inside Docker, LOCAL_POSTGRES_URI outside
    db_url = os.getenv("POSTGRES_URI") or os.getenv("LOCAL_POSTGRES_URI")
    if not db_url:
        raise ValueError("No database URL environment variable set")
    return db_url

def create_db_engine():
    """
    Create database engine with production settings:
    - Connection pooling with health checks
    - Automatic reconnection
    - Query timeout (set per-session for Neon compatibility)
    """
    db_url = get_database_url()
    
    # Check if using Neon pooler
    is_neon_pooler = 'pooler' in db_url and 'neon.tech' in db_url
    
    # Base connect_args
    connect_args = {
        "connect_timeout": 10,
        "sslmode": "disable"  # Required for Neon
    }
    
    # Only add statement_timeout if NOT using Neon pooler
    if not is_neon_pooler:
        connect_args["options"] = "-c statement_timeout=30000"
    
    engine = create_engine(
        db_url,
        # Connection pool settings
        poolclass=QueuePool,
        pool_size=20,              
        max_overflow=10,           
        pool_timeout=30,           
        pool_recycle=3600,         
        pool_pre_ping=True,        
        
        # Query settings
        echo=False,                
        echo_pool=False,           
        
        # Performance settings
        connect_args=connect_args
    )
    
    # Add connection pool event listeners for monitoring
    @event.listens_for(engine, "connect")
    def receive_connect(dbapi_conn, connection_record):
        logger.debug("Database connection established")
        
        # Set statement timeout for Neon pooler connections
        if is_neon_pooler:
            cursor = dbapi_conn.cursor()
            try:
                cursor.execute("SET statement_timeout = '30s'")
            finally:
                cursor.close()
    
    @event.listens_for(engine, "checkout")
    def receive_checkout(dbapi_conn, connection_record, connection_proxy):
        logger.debug("Connection checked out from pool")
    
    @event.listens_for(engine, "checkin")
    def receive_checkin(dbapi_conn, connection_record):
        logger.debug("Connection returned to pool")
    
    return engine


# Create engine ONCE at module import
try:
    engine = create_db_engine()
    logger.info("Database engine created successfully")
except Exception as e:
    logger.error(f"Failed to create database engine: {e}")
    raise


# Create session factory
session_maker = sessionmaker(
    bind=engine,
    class_=Session,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False
)


# ===== DATABASE INITIALIZATION =====

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((OperationalError, DatabaseError)),
    reraise=True
)
def init_db():
    """
    Initialize database with retry logic.
    Creates all tables if they don't exist.
    Also ensures the 'file_content' column exists in 'brand_documents'.
    """
    try:
        logger.info("Initializing database...")
        Model.metadata.create_all(engine)
        
        # Ensure 'file_content' column exists (self-healing migration)
        try:
            from uuid import uuid4
            from sqlalchemy import text
            with engine.begin() as conn:
                # Check if column exists
                check_sql = text("""
                    SELECT 1 
                    FROM information_schema.columns 
                    WHERE table_name='brand_documents' 
                    AND column_name='file_content'
                """)
                result = conn.execute(check_sql).fetchone()
                
                if not result:
                    logger.info("Adding missing 'file_content' column to 'brand_documents'...")
                    conn.execute(text("ALTER TABLE brand_documents ADD COLUMN file_content TEXT"))
                    logger.info("Successfully added 'file_content' column")

                # Regeneration provenance columns on reviewer_learning. Added
                # the same self-healing way so an existing deployment does not
                # need a migration step before the human-in-the-loop fix works.
                for table, column, ddl in (
                    ("reviewer_learning", "use_search", "ALTER TABLE reviewer_learning ADD COLUMN use_search BOOLEAN NOT NULL DEFAULT FALSE"),
                    ("reviewer_learning", "regeneration_depth", "ALTER TABLE reviewer_learning ADD COLUMN regeneration_depth INTEGER NOT NULL DEFAULT 0"),
                    ("brand_documents", "doc_role", "ALTER TABLE brand_documents ADD COLUMN doc_role VARCHAR(20) NOT NULL DEFAULT 'voice'"),
                ):
                    exists = conn.execute(text("""
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_name=:tbl
                        AND column_name=:col
                    """), {"tbl": table, "col": column}).fetchone()
                    if not exists:
                        logger.info("Adding missing '%s' column to '%s'...", column, table)
                        conn.execute(text(ddl))
                        logger.info("Successfully added '%s' column", column)


        except Exception as migration_error:
            logger.warning(f"Self-healing migration failed (non-critical): {migration_error}")

        logger.info("Database initialization completed")
        return True
    except Exception as e:
        logger.error(f"Database initialization failed: {e}", exc_info=True)
        raise


# ===== SESSION MANAGEMENT =====

# Use for codebase and scripts that need direct session access, and also for the streaming endpoint in fastAPI
@contextmanager
def get_db_session():
    """
    Context manager for database sessions with automatic cleanup.
    Callers are responsible for calling session.commit() explicitly.
    Rolls back on unhandled exceptions.

    Usage:
        with get_db_session() as session:
            session.add(record)
            session.commit()
    """
    session = session_maker()
    try:
        yield session
    except Exception as e:
        session.rollback()
        logger.error(f"Database session error: {e}", exc_info=True)
        raise
    finally:
        session.close()

# Use for FastAPI endpoints with dependency injection
def get_db():
    session = session_maker()
    try:
        yield session
    except Exception as e:
        session.rollback()
        raise
    finally:
        session.close()


# ===== HEALTH CHECK =====

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    retry=retry_if_exception_type((OperationalError, DatabaseError))
)
def check_database_health() -> bool:
    """
    Check if database is accessible and healthy.
    
    Returns:
        bool: True if database is healthy
    """
    try:
        with get_db_session() as session:
            # Simple query to test connection
            session.execute(func.now())
        return True
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return False



def get_pool_status() -> dict:
    """Get current connection pool status for monitoring"""
    pool = engine.pool
    return {
        "size": pool.size(),
        "checked_in": pool.checkedin(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
        "total": pool.size() + pool.overflow()
    }