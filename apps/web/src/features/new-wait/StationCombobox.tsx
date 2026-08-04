import { useEffect, useState, type KeyboardEvent } from "react";
import {
  CaretDown,
  CheckCircle,
  MagnifyingGlass,
  WarningCircle,
} from "@phosphor-icons/react";

import { rankedStationOptions, type StationSearchItem } from "./stationSearch";

export type StationComboboxStation = StationSearchItem & {
  cityCode?: string;
};

export type StationComboboxProps = {
  label: "출발역" | "도착역";
  value: string;
  selectedNodeId: string | null;
  onChange: (station: { name: string; nodeId: string | null }) => void;
  stations: ReadonlyArray<StationComboboxStation>;
  disabled?: boolean;
  loading?: boolean;
  error?: string;
};

export function StationCombobox({
  label,
  value,
  selectedNodeId,
  onChange,
  stations,
  disabled = false,
  loading = false,
  error = "",
}: StationComboboxProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState(value);
  const [activeIndex, setActiveIndex] = useState(0);
  const [touched, setTouched] = useState(false);
  const listId = `${label === "출발역" ? "origin" : "destination"}-station-list`;
  const helperId = `${listId}-helper`;
  const errorId = `${listId}-error`;
  const visibleError = touched ? error : "";
  const options = rankedStationOptions(stations, query);
  const duplicateNameCounts = stations.reduce((counts, station) => {
    counts.set(station.name, (counts.get(station.name) ?? 0) + 1);
    return counts;
  }, new Map<string, number>());

  useEffect(() => setQuery(value), [value]);
  useEffect(() => setActiveIndex(0), [query, stations]);
  useEffect(() => {
    if (disabled || loading) setTouched(false);
  }, [disabled, loading]);

  const choose = (station: StationComboboxStation): void => {
    setTouched(true);
    onChange(station);
    setQuery(station.name);
    setOpen(false);
    setActiveIndex(0);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((index) => open ? Math.min(index + 1, Math.max(0, options.length - 1)) : 0);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((index) => open ? Math.max(0, index - 1) : Math.max(0, options.length - 1));
    } else if (event.key === "Enter" && open && options[activeIndex]) {
      event.preventDefault();
      choose(options[activeIndex]);
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div className={visibleError ? "journey-field station-field has-error" : "journey-field station-field"}>
      <label htmlFor={`${listId}-input`}>{label}</label>
      <div className="combobox-shell">
        <MagnifyingGlass size={19} aria-hidden="true" />
        <input
          id={`${listId}-input`}
          role="combobox"
          aria-autocomplete="list"
          aria-controls={listId}
          aria-expanded={open}
          aria-activedescendant={open && options[activeIndex] ? `${listId}-${activeIndex}` : undefined}
          aria-describedby={visibleError ? `${helperId} ${errorId}` : helperId}
          aria-invalid={Boolean(visibleError)}
          aria-busy={loading}
          autoComplete="off"
          disabled={disabled}
          placeholder={loading ? "역 목록을 불러오는 중…" : disabled ? "운영사를 먼저 선택하세요" : "역 이름 또는 지역 검색"}
          value={query}
          onFocus={() => setOpen(true)}
          onClick={() => setOpen(true)}
          onBlur={() => setTouched(true)}
          onChange={(event) => {
            setTouched(true);
            setQuery(event.target.value);
            onChange({ name: event.target.value, nodeId: null });
            setActiveIndex(0);
            setOpen(true);
          }}
          onKeyDown={handleKeyDown}
        />
        <button
          type="button"
          className="combobox-toggle"
          disabled={disabled}
          aria-label={`${label} 목록 ${open ? "닫기" : "열기"}`}
          onClick={() => setOpen((current) => !current)}
        >
          <CaretDown size={18} aria-hidden="true" />
        </button>
      </div>
      <span id={helperId} className="station-field-helper">
        {loading ? "공식 역 목록을 불러오고 있습니다." : disabled ? "역 목록을 확인한 뒤 선택할 수 있습니다." : `${stations.length}개 역에서 검색`}
      </span>
      {visibleError && (
        <span id={errorId} className="station-field-error" role="alert">
          <WarningCircle size={15} weight="fill" aria-hidden="true" />
          {visibleError}
        </span>
      )}
      {open && (
        <>
          <button type="button" className="popover-scrim" aria-label={`${label} 선택 닫기`} onClick={() => setOpen(false)} />
          <div className="journey-popover station-popover">
            <div className="sheet-handle" aria-hidden="true" />
            <div className="popover-heading"><strong>{label} 선택</strong><span>역 이름이나 지역을 검색하세요</span></div>
            <div id={listId} role="listbox" aria-label={`${label} 검색 가능한 역`} className="station-options">
              {options.map((station, index) => (
                <button
                  id={`${listId}-${index}`}
                  key={station.nodeId}
                  role="option"
                  aria-selected={station.nodeId === selectedNodeId}
                  type="button"
                  tabIndex={-1}
                  className={index === activeIndex || station.nodeId === selectedNodeId ? "station-option is-active" : "station-option"}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => choose(station)}
                >
                  <span>
                    <strong>{station.name}</strong>
                    {station.cityName && (
                      <small>
                        {station.cityName}
                        {duplicateNameCounts.get(station.name) !== undefined && (duplicateNameCounts.get(station.name) ?? 0) > 1
                          ? ` · 역 코드 ${station.nodeId}`
                          : ""}
                      </small>
                    )}
                  </span>
                  {station.nodeId === selectedNodeId && <CheckCircle size={20} weight="fill" aria-hidden="true" />}
                </button>
              ))}
              {!options.length && <p className="empty-options">일치하는 역이 없습니다.</p>}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
