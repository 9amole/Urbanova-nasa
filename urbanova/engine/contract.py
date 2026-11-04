"""The engine's versioned JSON contract (request and result), as Pydantic models.

`AnalysisRequest.model_json_schema()` / `AnalysisResult.model_json_schema()` are exported to
`schemas/` by `python -m urbanova.engine schema`. Field descriptions are part of the contract.
Breaking changes increase the major version of CONTRACT_VERSION; additions increase the minor.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "1.0"

FeatureType = Literal["site_boundary", "building", "road", "green_space", "tree", "parking", "open_space", "water",
                      "shade_structure", "land_use_zone", "unclassified"]
Objective = Literal["main_concerns", "surface_heat", "vegetation", "shade", "compare_alternatives", "planning_compliance", "data_gaps"]
ModuleName = Literal["climate_baseline", "site_context", "urban_form", "vegetation", "surface_heat", "solar_shade", "planning_compliance"]
RuleType = Literal["max_building_coverage_pct", "max_height_m", "max_floors", "min_setback_m", "min_green_space_pct", "min_open_space_pct",
                   "max_far", "plot_area_m2", "permitted_land_use", "parking_requirement"]
ValueState = Literal["available", "unknown", "unavailable", "invalid", "blocked", "not_applicable"]
OutputKind = Literal["observed", "reanalysis", "calculated", "classified", "modelled", "user_provided"]
ModuleStatus = Literal["completed", "partial", "blocked", "unavailable", "failed", "skipped"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ request
class ClimatePeriod(_Strict):
    start_year: int = Field(ge=1981, description="First calendar year of the climate baseline.")
    end_year: int = Field(description="Last completed calendar year; at most 30 years after start_year.")


class Site(_Strict):
    latitude: float = Field(ge=-90, le=90, description="WGS 84 latitude of the site, decimal degrees.")
    longitude: float = Field(ge=-180, le=180, description="WGS 84 longitude of the site, decimal degrees.")
    boundary: dict[str, Any] | None = Field(None, description="Site boundary as a GeoJSON Polygon or MultiPolygon (or a Feature / FeatureCollection holding one), WGS 84. "
                                                              "Without it, site statistics use a 100 m radius around the point and site-relative ratios are blocked.")
    verified_site_area_m2: float | None = Field(None, gt=0, description="Plot area from a verified planning document, used instead of the boundary area for ratios when set.")
    climate_period: ClimatePeriod | None = Field(None, description="Climate baseline period. Default: the last 5 complete calendar years.")


class Design(_Strict):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$", description="Caller's identifier for the design; echoed in the result.")
    name: str = Field(min_length=1, max_length=160)
    kind: Literal["baseline", "alternative"] = Field(description="Exactly one design may be the baseline (the original design).")
    features: dict[str, Any] = Field(description="GeoJSON FeatureCollection in WGS 84. Every feature needs properties.feature_type (see FeatureType). "
                                                 "Optional properties: height_m, floors, canopy_radius_m (point trees), name, land_use. "
                                                 "Use extract_plan() to produce reviewed features from GeoJSON/DXF uploads.")


class RuleSource(_Strict):
    document: str = Field(min_length=1, description="Document, authority or clause the rule comes from.")
    page: int | None = Field(None, ge=1)
    text: str | None = Field(None, description="Exact wording of the condition, kept as evidence (any language).")


class VerifiedRule(_Strict):
    id: str | None = Field(None, max_length=64, description="Caller's rule identifier; generated when omitted.")
    rule_type: RuleType
    value: float | None = Field(None, description="Numeric limit (required for numeric rule types).")
    value_text: str | None = Field(None, description="Text value for permitted_land_use / parking_requirement.")
    source: RuleSource


class Rules(_Strict):
    verified: list[VerifiedRule] = Field(default_factory=list, description="Only user-verified rules are applied. At most one per rule_type.")
    unverified_count: int = Field(0, ge=0, description="Number of candidate rules not yet verified; reported as a warning, never applied.")
    conflicting_plot_areas_m2: list[float] = Field(default_factory=list, description="Plot areas stated differently in the documents and not yet resolved. "
                                                                                      "Two or more values block ratio checks until resolved.")


class Rate(_Strict):
    id: str | None = Field(None, max_length=64)
    quantity_type: Literal["green_space_added_m2", "hard_surface_removed_m2", "trees_added_count", "shade_structure_added_m2", "building_footprint_removed_m2"]
    unit: str
    rate: float = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3, description="ISO 4217 code, e.g. EGP; XTS for test values.")
    price_date: str = Field(description="YYYY-MM-DD")
    source: str = Field(min_length=1, description="Quotation reference, supplier or schedule of rates. Prices are never invented.")
    region: str | None = None


class Criterion(_Strict):
    metric: str
    weight: float = Field(gt=0)


class CompareRequest(_Strict):
    baseline_id: str
    alternative_ids: list[str] = Field(min_length=1, max_length=5)
    criteria: list[Criterion] | None = Field(None, description="Optional weighted scoring; omit for a plain comparison.")


class Settings(_Strict):
    modules: list[ModuleName] | None = Field(None, description="Modules to run. Default: derived from the objectives.")
    hot_months: list[int] | None = Field(None, description="Override of the hot-season months (1-12). Default: the 3 hottest months of the climate record.")
    solar_date: str | None = Field(None, description="Date (YYYY-MM-DD) for the shade analysis. Default: the 21st of the middle hottest month.")
    context_buffer_m: float | None = None
    lst_buffer_m: float | None = None
    ndvi_buffer_m: float | None = None
    satellite_years: int | None = None
    lst_max_scenes: int | None = None
    ndvi_max_scenes: int | None = None
    ndvi_lookback_days: int | None = None
    max_scene_cloud_pct: float | None = None
    min_valid_site_fraction: float | None = None


class AnalysisRequest(_Strict):
    contract_version: Literal["1.0"]
    request_id: str | None = Field(None, max_length=64, description="Caller's identifier; echoed in the result and used as the project id in provenance.")
    site: Site
    objectives: list[Objective] = Field(default_factory=lambda: ["main_concerns"])
    designs: list[Design] = Field(default_factory=list, max_length=6, description="0 designs = site-only analysis. At most 1 baseline + 5 alternatives. "
                                                                                  "All designs share the same site data and settings, so they are comparable.")
    rules: Rules = Field(default_factory=Rules)
    rates: list[Rate] = Field(default_factory=list)
    settings: Settings = Field(default_factory=Settings)
    compare: CompareRequest | None = None


# ------------------------------------------------------------------ result
class Metric(BaseModel):
    id: str
    metric_name: str
    label: str
    module: str
    state: ValueState = Field(description="Missing values are never 0: a value is present only when state is 'available'.")
    value: float | None
    unit: str | None
    reason: str | None = Field(None, description="Why the value is not available.")
    output_kind: OutputKind
    method: str | None
    design_dependent: int
    source_dataset_ids: list[str] = []
    quality: dict[str, Any] | None = None
    uncertainty: Any = None
    extra: dict[str, Any] | None = None


class Finding(BaseModel):
    id: str
    category: str
    classification: Literal["environmental_indicator", "potential_environmental_concern", "planning_rule_check", "data_quality_issue", "modelled_outcome"]
    title: str
    summary: str
    severity: Literal["high", "moderate", "low", "info"]
    severity_rule: str
    affected_area: str | None
    geometry: dict[str, Any] | None
    rule_id: str | None
    threshold: dict[str, Any] | None = Field(description="Includes status 'provisional_not_approved' for unapproved bands; such findings never exceed 'moderate'.")
    evidence: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    confidence: str
    limitations: list[str]


class Recommendation(BaseModel):
    id: str
    finding_id: str
    title: str
    problem: str
    intervention: str
    expected_direction: str = Field(description="Direction of impact only; never a quantified prediction.")
    required_inputs: list[str]
    tradeoffs: list[str]
    evaluation_method: str
    target: dict[str, Any]
    review_status: str


class Layer(BaseModel):
    id: str
    module: str
    name: str
    layer_kind: str
    output_kind: OutputKind
    file: str = Field(description="Path relative to the output directory (PNG overlay in Web Mercator, or GeoJSON).")
    bounds: dict[str, float] | None = Field(None, description="WGS 84 south/west/north/east of a raster overlay.")
    legend: Any = None
    meta: dict[str, Any] = Field(default_factory=dict, description="geotiff: relative path of the analysis-grade GeoTIFF in the local UTM CRS, when produced.")


class ModuleOutcome(BaseModel):
    status: ModuleStatus
    label: str
    reason: str | None = None
    warnings: list[str] = []
    limitations: list[str] = []
    details: dict[str, Any] = {}
    provider_failures: list[dict[str, Any]] = []
    shared_across_designs: bool = Field(False, description="True when this outcome was computed once for the site and reused for this design.")


class ObjectiveAnswer(BaseModel):
    objective: str
    question: str
    answerable: bool
    supported_by: list[str] = []
    not_available: list[str] = []
    explanation: str | None = None


class DesignResult(BaseModel):
    design_id: str | None = Field(description="None for a site-only analysis.")
    design_name: str | None
    kind: Literal["baseline", "alternative"] | None
    status: Literal["succeeded", "partially_succeeded", "failed"]
    modules: dict[str, ModuleOutcome]
    metrics: list[Metric]
    findings: list[Finding]
    recommendations: list[Recommendation]
    layers: list[Layer]
    objectives: list[ObjectiveAnswer]
    warnings: list[str]


class Dataset(BaseModel):
    id: str
    source_id: str
    dataset_type: str
    data_nature: str | None
    approval_status: str | None
    approval_label: str
    terms_confirmed: bool
    license: str | None
    attribution: str | None
    model_config = ConfigDict(extra="allow")


class AnalysisResult(BaseModel):
    contract_version: str
    engine_version: str
    analysis_version: str = Field(description="Version of the analysis methods; results with different versions are not comparable.")
    request_id: str | None
    input_fingerprint: str = Field(description="SHA-256 (16 hex) of the normalised request; identical inputs give an identical fingerprint.")
    generated_at: str
    usage_context: Literal["non_commercial_evaluation", "commercial"]
    status: Literal["succeeded", "partially_succeeded", "failed"]
    designs: list[DesignResult]
    datasets: list[Dataset]
    provider_requests: list[dict[str, Any]] = Field(description="One entry per outbound request or cache hit; secrets are redacted.")
    comparison: dict[str, Any] | None
    warnings: list[str]
    limitations: list[str] = Field(description="Statements that apply to every result of this build (e.g. provisional thresholds).")
