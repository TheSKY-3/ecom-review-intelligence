# Assumptions

- ASSUMPTION: The official Appliances review and metadata files remain available
  from the documented McAuley Lab URLs.
- ASSUMPTION: The local drive has enough free space for the P0 rule:
  2.5 times compressed input bytes plus 2 GiB when source sizes are known.
- DESIGN_DECISION: The first MVP uses one data-selected Appliances subcategory,
  not a preselected product type.
- DESIGN_DECISION: The project starts locally and does not depend on an LLM
  provider during P0–P5.
