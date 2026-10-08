// Shared building blocks for the Intelligence and Strategy pages.
// Layout classes live in src/styles/intel.css (prefix `ix-`).
export { default as PageHeader } from './PageHeader';
export { FilterSidebar, FilterGroup, SidebarItem, SidebarDivider } from './FilterSidebar';
export { default as DetailHeader } from './DetailHeader';
export { Tabs, TabPanel, useTabParam } from './Tabs';
export { default as Panel } from './Panel';
export { default as RegionChips, RegionDot } from './RegionChips';
export { TrendBadge, SupplyBadge, StatusBadge, KraljicBadge, GemstoneTag } from './Badges';
export { default as EmptyState, ErrorState } from './EmptyState';
export { Skeleton, SkeletonLines, LoadingCards, LoadingPanel, LoadingPage } from './Loading';
export { default as KpiTile } from './KpiTile';
export { default as useApi } from './useApi';
export { default as fmt } from './fmt';
export {
  SUBFAMILY_LABEL, SUBFAMILY_LABEL_PLURAL, UNPUBLISHED_LINE, STATUS_CODES, DEFAULT_STATUSES, STATUS_LABELS,
  parseStatusParam, statusParamOf, statusQuery, isDefaultStatus, isAllStatuses, lineFlags,
  productHref, lineHref, supplierHref, industryHref, strategyHref, productsHref, linesHref,
} from './vocab';
export {
  REGION_CODES, REGION_COLORS, REGION_VARS, REGION_LABELS, normalizeRegion, regionColor, sortRegions,
  GEMSTONES, gemstone, gemstoneColor,
  KRALJIC_QUADRANTS, KRALJIC_SPACE, kraljicQuadrant, normalizeKraljicBadge,
  SERIES_PALETTE, seriesColor,
} from './palette';
