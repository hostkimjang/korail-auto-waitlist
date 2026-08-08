"""Compatibility facade for the canonical Pydoll search DOM driver owner."""

from __future__ import annotations

from .korail_sidecar.pydoll import search_driver as _owner
from .korail_sidecar.pydoll.search_driver import Callable as Callable

Any = _owner.Any
Awaitable = _owner.Awaitable
BrowserSourceUnavailable = _owner.BrowserSourceUnavailable
Collection = _owner.Collection
EvaluateText = _owner.EvaluateText
EvaluateValue = _owner.EvaluateValue
ExecuteScript = _owner.ExecuteScript
Mapping = _owner.Mapping
Protocol = _owner.Protocol
PydollPageSnapshot = _owner.PydollPageSnapshot
PydollSearchDomDriver = _owner.PydollSearchDomDriver
PydollSeatBox = _owner.PydollSeatBox
PydollTrainRow = _owner.PydollTrainRow
QueryElement = _owner.QueryElement
SearchControlState = _owner.SearchControlState
SearchDomCompatibilityPort = _owner.SearchDomCompatibilityPort
SearchHourCandidate = _owner.SearchHourCandidate
SnapshotMerge = _owner.SnapshotMerge
SnapshotStop = _owner.SnapshotStop
SnapshotTransform = _owner.SnapshotTransform
TrainRowIdentity = _owner.TrainRowIdentity
advance_search_expansion = _owner.advance_search_expansion
annotations = _owner.annotations
begin_search_expansion = _owner.begin_search_expansion
dataclass = _owner.dataclass
date = _owner.date
protection_trigger_from_text = _owner.protection_trigger_from_text
re = _owner.re

del _owner
