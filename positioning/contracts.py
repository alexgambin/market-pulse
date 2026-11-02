"""Identidades y contratos de procedencia; no contienen una fórmula implícita."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Mapping


API_VERSION = "positioning-api-v1"
SCHEMA_VERSION = "positioning-schema-v1.2"
AVAILABILITY_VERSION = "positioning-availability-local-v1"

# Nunca equiparar participantes entre familias aunque sus etiquetas se parezcan.
PARTICIPANTS = {
    "LEGACY": frozenset({"NON_COMMERCIAL", "COMMERCIAL", "NON_REPORTABLE"}),
    "DISAGGREGATED": frozenset({"PRODUCER_MERCHANT_PROCESSOR_USER", "SWAP_DEALER",
                               "MANAGED_MONEY", "OTHER_REPORTABLE", "NON_REPORTABLE"}),
    "TFF": frozenset({"DEALER_INTERMEDIARY", "ASSET_MANAGER_INSTITUTIONAL",
                     "LEVERAGED_FUNDS", "OTHER_REPORTABLE", "NON_REPORTABLE"}),
}
DATASETS = {"LEGACY": "6dca-aqww", "DISAGGREGATED": "72hh-3qpy", "TFF": "gpe5-46if"}

AVAILABILITY_POLICY = {
    "version": AVAILABILITY_VERSION,
    "local_availability": "known_at: transaction visibility, never earlier than fetched_at",
    "historical_backfill": "known only at local ingestion; no inferred historical availability",
    "published_at": "nullable, supported by release evidence; not a scheduled timestamp",
    "revisions": "append-only; select latest revision known at the requested cutoff",
    "time_format": "UTC ISO-8601 with microseconds and +00:00",
}


def canonical_json(value) -> str:
    """JSON reproducible; rechaza NaN/Infinity, no los convierte en null/cero."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def content_hash(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp sin zona horaria")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class SeriesIdentity:
    instrument: str                 # clave EXACTA de precios.INSTRUMENTOS
    report_type: str
    participant_category: str
    market_code: str                # conserva ceros y '+'
    mapping_version: str
    source: str = "CFTC"
    report_scope: str = "FUTURES_ONLY"

    def __post_init__(self):
        if self.source != "CFTC" or self.report_scope != "FUTURES_ONLY":
            raise ValueError("fuente/alcance aún no soportado por este contrato CFTC")
        if self.participant_category not in PARTICIPANTS.get(self.report_type, ()):
            raise ValueError("participante incompatible con la familia CFTC")
        for value in (self.instrument, self.market_code, self.mapping_version):
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError("identidad vacía o no canónica")

    @property
    def dataset_id(self) -> str:
        return DATASETS[self.report_type]

    @property
    def series_id(self) -> str:
        return "cftc:" + content_hash(asdict(self))

    def validate_price_universe(self, instruments: Mapping[str, str]) -> None:
        """Estar en precios es necesario, pero NO valida una equivalencia CFTC."""
        if not instruments.get(self.instrument):
            raise ValueError("MAPPING_UNAVAILABLE: instrumento fuera del universo con precios")
