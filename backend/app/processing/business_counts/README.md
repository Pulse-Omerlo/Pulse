# Business counts

`quebec_cma.json` holds the number of employer businesses in each NAICS sector for the
census metropolitan areas entirely in Quebec, from Statistics Canada's *Canadian Business
Counts, with employees, census metropolitan areas and census subdivisions*. The sector mix
compares a network with one of them when its settings name a `region`
(see `../sector_targets/README.md`).

- Licence: [Statistics Canada Open Licence](https://www.statcan.gc.ca/en/terms-conditions/open-licence),
  which allows commercial use. Every place that shows these figures carries the
  `attribution` stored in the file.
- Regional shares are over the businesses Statistics Canada classified; its unclassified
  businesses (`unclassified`) are left out, and the dashboard says so.
- Statistics Canada publishes a new release every six months under a new table id. Refresh
  the file with `uv run python -m app.load_business_counts`, which finds the newest table by
  its title, then commit it.
