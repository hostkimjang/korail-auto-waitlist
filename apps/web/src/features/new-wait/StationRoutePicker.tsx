import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import {
  ArrowsLeftRight,
  CaretDown,
  CheckCircle,
  MagnifyingGlass,
  WarningCircle,
  X,
} from "@phosphor-icons/react";

import { useDocumentScrollLock } from "../../hooks/useDocumentScrollLock";
import { StationCombobox, type StationComboboxStation } from "./StationCombobox";
import { rankedStationOptions } from "./stationSearch";

type RouteField = "origin" | "destination";

type StationSelection = {
  name: string;
  nodeId: string | null;
};

type StationRoutePickerProps = {
  origin: StationSelection;
  destination: StationSelection;
  originError?: string;
  destinationError?: string;
  stations: ReadonlyArray<StationComboboxStation>;
  disabled?: boolean;
  loading?: boolean;
  onOriginChange: (station: StationSelection) => void;
  onDestinationChange: (station: StationSelection) => void;
  onSwap: () => void;
};

type StationTriggerFieldProps = {
  field: RouteField;
  selection: StationSelection;
  error: string;
  touched: boolean;
  disabled: boolean;
  loading: boolean;
  stationCount: number;
  expanded: boolean;
  triggerRef: RefObject<HTMLButtonElement | null>;
  onOpen: () => void;
};

const dialogMediaQuery = "(max-width: 980px), (any-pointer: coarse)";
const featuredRegion = "주요";
const featuredStationNames = ["서울", "용산", "대전", "동대구", "부산", "수서"] as const;
const regionOrder = ["서울", "경기", "충청", "전라", "경상", "강원", "제주", "기타"] as const;

function fieldLabel(field: RouteField): "출발역" | "도착역" {
  return field === "origin" ? "출발역" : "도착역";
}

function stationRegion(station: StationComboboxStation): string {
  return station.cityName?.trim() || "기타";
}

function stationRegionGroup(station: StationComboboxStation): string {
  const region = stationRegion(station);
  if (region.includes("서울")) return "서울";
  if (region.includes("경기") || region.includes("인천")) return "경기";
  if (region.includes("충청") || region.includes("대전") || region.includes("세종")) return "충청";
  if (region.includes("전라") || region.includes("광주")) return "전라";
  if (
    region.includes("경상")
    || region.includes("부산")
    || region.includes("대구")
    || region.includes("울산")
  ) return "경상";
  if (region.includes("강원")) return "강원";
  if (region.includes("제주")) return "제주";
  return "기타";
}

function useStationDialogLayout(): boolean {
  const [matches, setMatches] = useState(() => {
    if (typeof window === "undefined") return false;
    if (typeof window.matchMedia !== "function") return window.innerWidth <= 980;
    return window.matchMedia(dialogMediaQuery).matches;
  });

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return undefined;
    const media = window.matchMedia(dialogMediaQuery);
    const update = (): void => setMatches(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  return matches;
}

function StationTriggerField({
  field,
  selection,
  error,
  touched,
  disabled,
  loading,
  stationCount,
  expanded,
  triggerRef,
  onOpen,
}: StationTriggerFieldProps) {
  const label = fieldLabel(field);
  const fieldId = `${field}-station-dialog-trigger`;
  const helperId = `${fieldId}-helper`;
  const errorId = `${fieldId}-error`;
  const visibleError = touched ? error : "";
  const displayValue = selection.nodeId ? selection.name : "역 이름 또는 지역 검색";

  return (
    <div className={visibleError ? "journey-field station-field has-error" : "journey-field station-field"}>
      <span id={`${fieldId}-label`} className="station-trigger-label">{label}</span>
      <button
        ref={triggerRef}
        id={fieldId}
        type="button"
        className={selection.nodeId ? "station-dialog-trigger" : "station-dialog-trigger is-placeholder"}
        disabled={disabled}
        aria-haspopup="dialog"
        aria-expanded={expanded}
        aria-labelledby={`${fieldId}-label ${fieldId}-value`}
        aria-describedby={visibleError ? `${helperId} ${errorId}` : helperId}
        aria-invalid={Boolean(visibleError)}
        onClick={onOpen}
      >
        <MagnifyingGlass size={19} aria-hidden="true" />
        <span id={`${fieldId}-value`}>{displayValue}</span>
        <CaretDown size={18} aria-hidden="true" />
      </button>
      <span id={helperId} className="station-field-helper">
        {loading ? "공식 역 목록을 불러오고 있습니다." : disabled ? "역 목록을 확인한 뒤 선택할 수 있습니다." : `${stationCount}개 역에서 검색`}
      </span>
      {visibleError && (
        <span id={errorId} className="station-field-error" role="alert">
          <WarningCircle size={15} weight="fill" aria-hidden="true" />
          {visibleError}
        </span>
      )}
    </div>
  );
}

export function StationRoutePicker({
  origin,
  destination,
  originError = "",
  destinationError = "",
  stations,
  disabled = false,
  loading = false,
  onOriginChange,
  onDestinationChange,
  onSwap,
}: StationRoutePickerProps) {
  const dialogLayout = useStationDialogLayout();
  const [activeField, setActiveField] = useState<RouteField | null>(null);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [selectedRegion, setSelectedRegion] = useState(featuredRegion);
  const [touched, setTouched] = useState<Record<RouteField, boolean>>({
    origin: false,
    destination: false,
  });
  const originTriggerRef = useRef<HTMLButtonElement>(null);
  const destinationTriggerRef = useRef<HTMLButtonElement>(null);
  const lastTriggerRef = useRef<HTMLButtonElement | null>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const layerRef = useRef<HTMLDivElement>(null);
  const listId = "route-station-dialog-list";
  const dialogTitleId = "route-station-dialog-title";
  const open = dialogLayout && activeField !== null;
  useDocumentScrollLock(open);
  const regions = useMemo(() => {
    const availableGroups = new Set(stations.map(stationRegionGroup));
    return [featuredRegion, ...regionOrder.filter((region) => availableGroups.has(region))];
  }, [stations]);
  const stationsInRegion = useMemo(() => {
    if (selectedRegion !== featuredRegion) {
      return stations.filter((station) => stationRegionGroup(station) === selectedRegion);
    }
    return featuredStationNames.flatMap((name) => (
      stations.filter((station) => station.name === name)
    ));
  }, [selectedRegion, stations]);
  const options = useMemo(() => (
    query.trim()
      ? rankedStationOptions(stations, query)
      : rankedStationOptions(stationsInRegion, "")
  ), [query, stations, stationsInRegion]);
  const duplicateNameCounts = useMemo(() => stations.reduce((counts, station) => {
    counts.set(station.name, (counts.get(station.name) ?? 0) + 1);
    return counts;
  }, new Map<string, number>()), [stations]);
  const activeSelection = activeField === "origin" ? origin : destination;
  const currentActiveIndex = Math.min(activeIndex, Math.max(0, options.length - 1));

  const restoreTriggerFocus = (): void => {
    window.setTimeout(() => lastTriggerRef.current?.focus(), 0);
  };

  const closeDialog = (): void => {
    if (activeField) {
      setTouched((current) => ({ ...current, [activeField]: true }));
    }
    setActiveField(null);
    setQuery("");
    setSelectedRegion(featuredRegion);
    restoreTriggerFocus();
  };

  const activateField = (field: RouteField, restoreTo?: HTMLButtonElement | null): void => {
    lastTriggerRef.current = restoreTo
      ?? (field === "origin" ? originTriggerRef.current : destinationTriggerRef.current);
    setActiveField(field);
    setQuery("");
    setSelectedRegion(featuredRegion);
    setActiveIndex(0);
  };

  useEffect(() => {
    if (!open) return undefined;
    const appRoot = lastTriggerRef.current?.closest<HTMLElement>(".app-shell");
    const previousInert = appRoot?.inert ?? false;
    const previousAriaHidden = appRoot?.getAttribute("aria-hidden") ?? null;
    const layer = layerRef.current;
    const viewport = window.visualViewport;
    const updateViewport = (): void => {
      if (!layer) return;
      const height = viewport?.height ?? window.innerHeight;
      const width = viewport?.width ?? window.innerWidth;
      const top = viewport?.offsetTop ?? 0;
      const left = viewport?.offsetLeft ?? 0;
      layer.style.setProperty("--station-dialog-height", `${Math.round(height)}px`);
      layer.style.setProperty("--station-dialog-width", `${Math.round(width)}px`);
      layer.style.setProperty("--station-dialog-top", `${Math.round(top)}px`);
      layer.style.setProperty("--station-dialog-left", `${Math.round(left)}px`);
    };

    if (appRoot) {
      appRoot.inert = true;
      appRoot.setAttribute("aria-hidden", "true");
    }
    updateViewport();
    viewport?.addEventListener("resize", updateViewport);
    viewport?.addEventListener("scroll", updateViewport);
    window.addEventListener("resize", updateViewport);
    const focusTimer = window.setTimeout(() => {
      dialogRef.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    }, 0);

    return () => {
      window.clearTimeout(focusTimer);
      viewport?.removeEventListener("resize", updateViewport);
      viewport?.removeEventListener("scroll", updateViewport);
      window.removeEventListener("resize", updateViewport);
      if (appRoot) {
        appRoot.inert = previousInert;
        if (previousAriaHidden === null) appRoot.removeAttribute("aria-hidden");
        else appRoot.setAttribute("aria-hidden", previousAriaHidden);
      }
    };
  }, [open]);

  useEffect(() => {
    if (!open || !options[currentActiveIndex]) return;
    const activeOption = document.getElementById(`${listId}-${currentActiveIndex}`);
    if (activeOption && typeof activeOption.scrollIntoView === "function") {
      activeOption.scrollIntoView({ block: "nearest" });
    }
  }, [currentActiveIndex, open, options]);

  const chooseStation = (station: StationComboboxStation): void => {
    if (activeField === "origin") {
      const keepSearchFocus = document.activeElement === searchInputRef.current;
      onOriginChange(station);
      setTouched((current) => ({ ...current, origin: true }));
      lastTriggerRef.current = destinationTriggerRef.current;
      setActiveField("destination");
      setQuery("");
      setSelectedRegion(featuredRegion);
      setActiveIndex(0);
      if (keepSearchFocus) window.setTimeout(() => searchInputRef.current?.focus(), 0);
      return;
    }
    if (activeField === "destination") {
      onDestinationChange(station);
      setTouched((current) => ({ ...current, destination: true }));
      setActiveField(null);
      setQuery("");
      setSelectedRegion(featuredRegion);
      restoreTriggerFocus();
    }
  };

  const handleSearchKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.nativeEvent.isComposing) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => Math.min(index + 1, Math.max(0, options.length - 1)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => Math.max(0, index - 1));
    } else if (event.key === "Enter" && options[currentActiveIndex]) {
      event.preventDefault();
      chooseStation(options[currentActiveIndex]);
    }
  };

  const handleDialogKeyDown = (event: KeyboardEvent<HTMLElement>): void => {
    if (event.nativeEvent.isComposing) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeDialog();
      return;
    }
    if (event.key !== "Tab" || !dialogRef.current) return;
    const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
      'button:not(:disabled):not([tabindex="-1"]), input:not(:disabled), [href], select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
    ));
    const first = focusable[0];
    const last = focusable.at(-1);
    if (!first || !last) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  if (!dialogLayout) {
    return (
      <div className="route-fields">
        <StationCombobox
          label="출발역"
          value={origin.name}
          selectedNodeId={origin.nodeId}
          stations={stations}
          loading={loading}
          disabled={disabled}
          error={originError}
          onChange={onOriginChange}
        />
        <div className="route-swap-slot">
          <button
            className="swap-button"
            type="button"
            disabled={disabled || !origin.nodeId || !destination.nodeId}
            aria-label="출발역과 도착역 바꾸기"
            onClick={onSwap}
          >
            <ArrowsLeftRight size={23} aria-hidden="true" />
          </button>
        </div>
        <StationCombobox
          label="도착역"
          value={destination.name}
          selectedNodeId={destination.nodeId}
          stations={stations}
          loading={loading}
          disabled={disabled}
          error={destinationError}
          onChange={onDestinationChange}
        />
      </div>
    );
  }

  return (
    <>
      <div className="route-fields route-fields-dialog">
        <StationTriggerField
          field="origin"
          selection={origin}
          error={originError}
          touched={touched.origin}
          disabled={disabled}
          loading={loading}
          stationCount={stations.length}
          expanded={activeField === "origin"}
          triggerRef={originTriggerRef}
          onOpen={() => activateField("origin")}
        />
        <div className="route-swap-slot">
          <button
            className="swap-button"
            type="button"
            disabled={disabled || !origin.nodeId || !destination.nodeId}
            aria-label="출발역과 도착역 바꾸기"
            onClick={onSwap}
          >
            <ArrowsLeftRight size={23} aria-hidden="true" />
          </button>
        </div>
        <StationTriggerField
          field="destination"
          selection={destination}
          error={destinationError}
          touched={touched.destination}
          disabled={disabled}
          loading={loading}
          stationCount={stations.length}
          expanded={activeField === "destination"}
          triggerRef={destinationTriggerRef}
          onOpen={() => activateField("destination")}
        />
      </div>
      {open && activeField && createPortal((
        <div ref={layerRef} className="station-route-dialog-layer">
          <section
            ref={dialogRef}
            className="station-route-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby={dialogTitleId}
            onKeyDown={handleDialogKeyDown}
          >
            <div className="station-route-dialog-content">
              <header className="station-route-dialog-header">
                <div>
                  <span>{activeField === "origin" ? "1 / 2" : "2 / 2"}</span>
                  <h2 id={dialogTitleId}>여정 역 선택</h2>
                </div>
                <button data-autofocus type="button" className="icon-button" aria-label="역 선택 닫기" onClick={closeDialog}>
                  <X size={22} aria-hidden="true" />
                </button>
              </header>

              <div className="station-route-dialog-route" aria-label="선택한 여정">
                <button
                  type="button"
                  className={activeField === "origin" ? "station-route-slot is-active" : "station-route-slot"}
                  aria-pressed={activeField === "origin"}
                  aria-label={`출발역 ${origin.nodeId ? origin.name : "선택하세요"}`}
                  onClick={() => activateField("origin", lastTriggerRef.current)}
                >
                  <small>출발역</small>
                  <strong>{origin.nodeId ? origin.name : "선택하세요"}</strong>
                </button>
                <button
                  type="button"
                  className="station-route-dialog-swap"
                  disabled={!origin.nodeId || !destination.nodeId}
                  aria-label="선택창에서 출발역과 도착역 바꾸기"
                  onClick={onSwap}
                >
                  <ArrowsLeftRight size={22} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  className={activeField === "destination" ? "station-route-slot is-active" : "station-route-slot"}
                  aria-pressed={activeField === "destination"}
                  aria-label={`도착역 ${destination.nodeId ? destination.name : "선택하세요"}`}
                  onClick={() => activateField("destination", lastTriggerRef.current)}
                >
                  <small>도착역</small>
                  <strong>{destination.nodeId ? destination.name : "선택하세요"}</strong>
                </button>
              </div>

              <div className="station-route-dialog-search">
                <label htmlFor="route-station-dialog-search">
                  <span role="status" aria-live="polite">{fieldLabel(activeField)}을 선택하세요</span>
                </label>
                <div className="station-route-dialog-searchbox">
                  <MagnifyingGlass size={20} aria-hidden="true" />
                  <input
                    ref={searchInputRef}
                    id="route-station-dialog-search"
                    role="combobox"
                    aria-label={`${fieldLabel(activeField)} 검색`}
                    aria-autocomplete="list"
                    aria-controls={listId}
                    aria-expanded="true"
                    aria-activedescendant={options[currentActiveIndex] ? `${listId}-${currentActiveIndex}` : undefined}
                    autoComplete="off"
                    enterKeyHint={activeField === "origin" ? "next" : "done"}
                    placeholder="역 이름이나 지역을 검색하세요"
                    value={query}
                    onChange={(event) => {
                      setQuery(event.target.value);
                      setActiveIndex(0);
                    }}
                    onKeyDown={handleSearchKeyDown}
                  />
                  {query && (
                    <button
                      type="button"
                      aria-label="역 검색어 지우기"
                      onPointerDown={(event) => event.preventDefault()}
                      onClick={() => {
                        setQuery("");
                        setActiveIndex(0);
                        searchInputRef.current?.focus();
                      }}
                    >
                      <X size={19} aria-hidden="true" />
                    </button>
                  )}
                </div>
                <span>{query.trim() ? `검색 결과 ${options.length}개` : `${selectedRegion} · ${options.length}개 역`}</span>
              </div>

              <div className="station-route-dialog-browser">
                <nav className="station-region-list" aria-label="지역 선택">
                  {regions.map((region) => (
                    <button
                      key={region}
                      type="button"
                      aria-pressed={selectedRegion === region && !query.trim()}
                      className={selectedRegion === region && !query.trim() ? "is-active" : ""}
                      onClick={() => {
                        setSelectedRegion(region);
                        setQuery("");
                        setActiveIndex(0);
                      }}
                    >
                      {region}
                    </button>
                  ))}
                </nav>
                <div id={listId} role="listbox" aria-label={`${fieldLabel(activeField)} 검색 가능한 역`} className="station-route-results">
                  {options.map((station, index) => (
                    <button
                      id={`${listId}-${index}`}
                      key={station.nodeId}
                      role="option"
                      aria-selected={station.nodeId === activeSelection.nodeId}
                      type="button"
                      tabIndex={-1}
                      className={index === currentActiveIndex || station.nodeId === activeSelection.nodeId ? "station-route-result is-active" : "station-route-result"}
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => chooseStation(station)}
                    >
                      <span>
                        <strong>{station.name}</strong>
                        <small>
                          {stationRegion(station)}
                          {(duplicateNameCounts.get(station.name) ?? 0) > 1 ? ` · 역 코드 ${station.nodeId}` : ""}
                        </small>
                      </span>
                      {station.nodeId === activeSelection.nodeId && <CheckCircle size={21} weight="fill" aria-hidden="true" />}
                    </button>
                  ))}
                  {!options.length && <p className="empty-options">일치하는 역이 없습니다.</p>}
                </div>
              </div>
            </div>
          </section>
        </div>
      ), document.body)}
    </>
  );
}
