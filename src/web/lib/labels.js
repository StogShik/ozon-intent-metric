export const SEGMENT_LABELS = {
  is_in_product_base: "Found in product base",
  is_article: "Looks like article (артикул)",
  query_stratification_score: "NLP complexity score (0–5)",
  query_len_bucket: "Query length (words)",
  n_unique_queries_bucket: "Refinements per session",
  n_view_bucket: "Views per session",
  first_widget: "Entry widget",
  top_category_in_session: "Top category",
};

export function segmentLabel(segment) {
  return SEGMENT_LABELS[segment] || segment;
}
