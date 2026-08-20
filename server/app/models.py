from sqlalchemy import Column, String, Text, DateTime, ForeignKey

from .database import Base


class Registro(Base):
    """Registro de negocio. Puede haber sido creado localmente o haber
    llegado por sincronización desde el otro servidor."""

    __tablename__ = "registros"

    id = Column(String(36), primary_key=True)
    contenido = Column(Text, nullable=False)
    origen_servidor = Column(String(20), nullable=False)
    timestamp = Column(DateTime, nullable=False)


class ColaPendiente(Base):
    """Cola de sincronización: registros locales que aún no se han podido
    enviar al servidor par porque estaba caído o inalcanzable."""

    __tablename__ = "cola_pendientes"

    registro_id = Column(String(36), ForeignKey("registros.id"), primary_key=True)
    creado_en = Column(DateTime, nullable=False)
