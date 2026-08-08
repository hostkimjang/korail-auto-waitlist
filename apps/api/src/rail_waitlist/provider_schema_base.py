from pydantic import ConfigDict

from .schema_base import ApiModel


class ProviderContractModel(ApiModel):
    """Strict provider boundary that cannot carry arbitrary transport or secret fields."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")
