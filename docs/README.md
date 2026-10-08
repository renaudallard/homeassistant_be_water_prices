# Contributor documentation

This directory documents the internals of the **Belgian Water Prices** Home
Assistant integration (domain `be_water_prices`), for people who maintain or
extend it. End-user setup lives in the [project README](../README.md).

| Document | Covers |
| --- | --- |
| [architecture.md](architecture.md) | The big picture, the module map, the data flow, the design rules |
| [glossary.md](glossary.md) | Belgian water and Home Assistant terms |
| [pricing-model.md](pricing-model.md) | How a year of water is billed in each region |
| [coordinator.md](coordinator.md) | The daily refresh, what is kept, staleness, the year to date |
| [config-flow.md](config-flow.md) | The setup wizard, the options and reconfigure |
| [data-sources.md](data-sources.md) | The utilities' pages, the card archive, the recorder, postcodes, the regulated constants |
| [provider-framework.md](provider-framework.md) | The extractor protocol, the dataclasses, the shared readers, adding a utility |
| [entities.md](entities.md) | Sensors, button, services, Repairs, diagnostics |
| [ci-and-testing.md](ci-and-testing.md) | The tests, the workflows, the live and drift checks, the card archive |

One page per utility lives under [providers/](providers/): where its tariff
is, how each figure is read, its communes and its quirks.

## A note on prices

No utility price is typed into the source, bar AIEC's transcribed card, and
these pages follow the same rule: any figure quoted is a regulated one the
code carries, or one read off a real publication in the test fixtures, and
says so.
