"""Compatibility facade for the KORAIL Pydoll page contracts."""

from __future__ import annotations

from .korail_sidecar.pydoll import page_contracts as _owner

annotations = _owner.annotations
re = _owner.re
dataclass = _owner.dataclass
KORAIL_ROUTE_HEADING = _owner.KORAIL_ROUTE_HEADING
PydollSeatBox = _owner.PydollSeatBox
PydollTrainRow = _owner.PydollTrainRow
PydollPageSnapshot = _owner.PydollPageSnapshot
normalize_korail_station = _owner.normalize_korail_station
normalize_korail_train_number = _owner.normalize_korail_train_number
