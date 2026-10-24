import time, uuid
from sqlalchemy import Boolean, Float, Integer, String, Text, JSON, ForeignKey, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

def identifier(): return uuid.uuid4().hex
class Base(DeclarativeBase): pass
class User(Base):
    __tablename__='users'
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=identifier)
    name:Mapped[str]=mapped_column(String(120),unique=True)
    password:Mapped[str]=mapped_column(Text)
    role:Mapped[str]=mapped_column(String(20),default='customer')
    active:Mapped[bool]=mapped_column(Boolean,default=True)
class Login(Base):
    __tablename__='sessions'
    token_hash:Mapped[str]=mapped_column(String(64),primary_key=True)
    user_id:Mapped[str]=mapped_column(ForeignKey('users.id'))
    csrf:Mapped[str]=mapped_column(String(80))
    expires:Mapped[float]=mapped_column(Float)
class Document(Base):
    __tablename__='documents'
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=identifier)
    owner:Mapped[str]=mapped_column(ForeignKey('users.id'),index=True)
    filename:Mapped[str]=mapped_column(String(180))
    sha256:Mapped[str]=mapped_column(String(64))
    pages:Mapped[int]=mapped_column(Integer)
    created:Mapped[float]=mapped_column(Float,default=time.time)
class Job(Base):
    __tablename__='jobs'
    __table_args__=(UniqueConstraint('owner','idempotency_key'),)
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=identifier)
    owner:Mapped[str]=mapped_column(ForeignKey('users.id'),index=True)
    document_id:Mapped[str]=mapped_column(ForeignKey('documents.id'))
    status:Mapped[str]=mapped_column(String(24),default='queued',index=True)
    stage:Mapped[str]=mapped_column(String(80),default='queued')
    quality:Mapped[str]=mapped_column(String(24),default='not_reviewed')
    options:Mapped[dict]=mapped_column(JSON)
    metrics:Mapped[dict]=mapped_column(JSON,default=dict)
    baseline:Mapped[bool]=mapped_column(Boolean,default=False)
    baseline_key:Mapped[str|None]=mapped_column(String(150),unique=True,nullable=True)
    idempotency_key:Mapped[str]=mapped_column(String(120))
    request_hash:Mapped[str]=mapped_column(String(64))
    created:Mapped[float]=mapped_column(Float,default=time.time)
    updated:Mapped[float]=mapped_column(Float,default=time.time)
    heartbeat:Mapped[float|None]=mapped_column(Float,nullable=True)
    attempt:Mapped[str|None]=mapped_column(String(32),nullable=True)
    error_code:Mapped[str|None]=mapped_column(String(80),nullable=True)
class Artifact(Base):
    __tablename__='artifacts'
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=identifier)
    job_id:Mapped[str]=mapped_column(ForeignKey('jobs.id'),index=True)
    kind:Mapped[str]=mapped_column(String(40))
    storage_key:Mapped[str]=mapped_column(Text)
    filename:Mapped[str]=mapped_column(String(180))
    sha256:Mapped[str]=mapped_column(String(64))
    size:Mapped[int]=mapped_column(Integer)
class JobEvent(Base):
    __tablename__='job_events'
    id:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True)
    job_id:Mapped[str]=mapped_column(ForeignKey('jobs.id'),index=True)
    created:Mapped[float]=mapped_column(Float,default=time.time)
    stage:Mapped[str]=mapped_column(String(80))
    elapsed:Mapped[float]=mapped_column(Float,default=0)
class Review(Base):
    __tablename__='reviews'
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=identifier)
    job_id:Mapped[str]=mapped_column(ForeignKey('jobs.id'),index=True)
    reviewer:Mapped[str]=mapped_column(ForeignKey('users.id'))
    decision:Mapped[str]=mapped_column(String(30))
    note:Mapped[str]=mapped_column(Text)
    output_sha256:Mapped[str]=mapped_column(String(64))
    created:Mapped[float]=mapped_column(Float,default=time.time)
class WorkerState(Base):
    __tablename__='worker_state'
    id:Mapped[str]=mapped_column(String(32),primary_key=True)
    heartbeat:Mapped[float]=mapped_column(Float)
    status:Mapped[str]=mapped_column(String(30))
class Baseline(Base):
    __tablename__='baselines'
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=identifier)
    title:Mapped[str]=mapped_column(String(180))
    owner:Mapped[str]=mapped_column(ForeignKey('users.id'))
    created:Mapped[float]=mapped_column(Float,default=time.time)
class BaselineMember(Base):
    __tablename__='baseline_members'
    __table_args__=(UniqueConstraint('baseline_id','job_id'),)
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=identifier)
    baseline_id:Mapped[str]=mapped_column(ForeignKey('baselines.id'))
    job_id:Mapped[str]=mapped_column(ForeignKey('jobs.id'))
    output_sha256:Mapped[str]=mapped_column(String(64))
class Schema(Base):
    __tablename__='schema_version'
    version:Mapped[int]=mapped_column(Integer,primary_key=True)

def database(settings):
    settings.prepare()
    kw={'check_same_thread':False,'timeout':20} if settings.database_url.startswith('sqlite') else {}
    engine=create_engine(settings.database_url,connect_args=kw,pool_pre_ping=True)
    if engine.dialect.name=='sqlite':
        @event.listens_for(engine,'connect')
        def pragmas(connection,_):
            c=connection.cursor();c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA journal_mode=WAL');c.close()
    Base.metadata.create_all(engine)
    sessions=sessionmaker(engine,expire_on_commit=False)
    with sessions.begin() as s:
        versions=s.query(Schema).all()
        if not versions:s.add(Schema(version=1))
        elif [x.version for x in versions]!=[1]:raise RuntimeError('Unsupported database schema; do not auto-upgrade.')
    return engine,sessions
