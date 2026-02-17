## [1.3.0](https://github.com/HDRUK/project-daphne-nlp/compare/v1.2.0...v1.3.0) (2026-02-17)

### ✨ Features

* **DP-377:** Sets configurable maximum number of returned candidates weighted by ratio scoring ([7961271](https://github.com/HDRUK/project-daphne-nlp/commit/796127181eb468e2e27af974ceda576000796645)), closes [DP-377](DP-377)

## [1.2.0](https://github.com/HDRUK/project-daphne-nlp/compare/v1.1.0...v1.2.0) (2026-02-12)

### ✨ Features

* **DP-335:** #20 - A *VERY* experimental way of building acronym index layer from loaded concepts. ([ea72759](https://github.com/HDRUK/project-daphne-nlp/commit/ea72759d7817ce581c96b5d81b7fdf6857c3cf36)), closes [DP-335](DP-335)

## [1.1.0](https://github.com/HDRUK/project-daphne-nlp/compare/v1.0.0...v1.1.0) (2026-02-06)

### ✨ Features

* **DP-335-2:** Updates query parser to use less aggressive overlap and increase matching ([e20a2e2](https://github.com/HDRUK/project-daphne-nlp/commit/e20a2e22c88b0a4f216b1a4cf03b3c0ab34fc3c2)), closes [DP-335-2](DP-335-2)
* **DP-335:** #11 - needed to refactor some unruly code spaghetti and opted for config files and mappings ([0aee972](https://github.com/HDRUK/project-daphne-nlp/commit/0aee9725b8b97870601663dbe7b469ce253819ac)), closes [DP-335](DP-335)
* **DP-335:** #12 - updates to allow rule mappings for adults, child, children, senior and elderly. This helps map queries like: Adults with depression, and Children with Asthma more intelligently ([c130be5](https://github.com/HDRUK/project-daphne-nlp/commit/c130be5ff23fafe83df5024590b729bf8f1b75e3)), closes [DP-335](DP-335)
* **DP-335:** #14 - Significant refactoring to make more modular, also tweaks candidate splitting for demographics-only filtering to resolve Adults with type 2 diabetes diagnosed in the last 2 years, which didnt fully infer demographic from Adults with ([531b1f4](https://github.com/HDRUK/project-daphne-nlp/commit/531b1f4f9ba249c9063d987f973c6e9de3b7cc2c))
* **DP-335:** #16 - handles temporal squencing context and returns unsupported warnings ([4fe5da4](https://github.com/HDRUK/project-daphne-nlp/commit/4fe5da48a80a09e6c805a0138d291d5aef5e6345)), closes [DP-335](DP-335)
* **DP-335:** #17 - Tweaks to to handle constraints to ages, and those inferred as general query scope ([5c00166](https://github.com/HDRUK/project-daphne-nlp/commit/5c0016664cbd5bbac35d1157785a05ffda5a460a)), closes [DP-335](DP-335)
* **DP-335:** #8 - further optimisations to better handle demographic forms that don't conform normally to omop concept for MALE and FEMALE ([8d778f1](https://github.com/HDRUK/project-daphne-nlp/commit/8d778f1a345889b95683f35937e7b841a4e158bf)), closes [DP-335](DP-335)
* **DP-335:** Adds distinct to query to reduce latency when loading potentially duplicate concepts ([c3048fb](https://github.com/HDRUK/project-daphne-nlp/commit/c3048fbbb4e6b5b0733daecc944f058906247316)), closes [DP-335](DP-335)
* **DP-335:** Adds logging to explain tokenisation, cleansing and matching vs scores from loaded concepts ([f31528e](https://github.com/HDRUK/project-daphne-nlp/commit/f31528ecdd1ba53b33c59d8e4827dbb466e8cdc6)), closes [DP-335](DP-335)
* **DP-335:** implements background refresh to deal with stale data but reduce networking locking in event that concepts are updated - it will continue to use the pre-loaded resolver, while a refresh is happening and the return the new once completed ([ef15d0d](https://github.com/HDRUK/project-daphne-nlp/commit/ef15d0d85a677ac7a30dd30b0d8f5c17fa61b6b6)), closes [DP-335](DP-335)

## 1.0.0 (2026-01-14)

### ✨ Features

* **DP-101:** Implements core services to serve fuzzy logic for daphne query parsing ([e2eac26](https://github.com/HDRUK/project-daphne-nlp/commit/e2eac260c7a85121ab957298f1bd95a3beac7ea2)), closes [DP-101](DP-101)
* **DP-334:** Updates to NLP query handling ([0fe3326](https://github.com/HDRUK/project-daphne-nlp/commit/0fe332697a468c510cf122acddb83495ea55ddd2)), closes [DP-334](DP-334)
