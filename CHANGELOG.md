# PUB-TOOL runtime changes

## 0.3.1

- Add canvas revision deletion and opt-in retention, stable node display numbers,
  RWTD-style connection routing and query/resolver execution states.
- Add grouped Style Settings and local WebEngine compatibility preferences.
- Update the in-APP upper-left logo and tagline to "Beyond the Search."
- Add EndNote RIS export with abstracts and saved screening notes.
- Add the LM Studio/OpenAI-compatible local connection interface, model listing
  and an explicit text-response test. No automatic model deployment or screening.
- Inject the bundled Qt WebChannel script before frontend initialization and
  report connection failures without a misleading fallback version number.
- Include Python Debug support modules in the update runtime. Development
  source checkouts have a version/local-Git toolbar and reviewed CSS persistence;
  normal installations keep source-write controls disabled.
- Publish the runtime through local foreground tools. No GitHub Actions
  workflows or Launcher binaries are included in this update payload.

Source hot reload and local Git snapshots require the full development checkout.
This update does not rebuild an existing Launcher. New Launcher shortcut/GPU UI
customizations require a separately built local installer.

The user-run 2026-11-05 16:27 checks recorded 151 Python tests, 12 frontend tests
and 8 Launcher policy tests passing. Publication runs fresh build/check steps;
installation, real updates and EndNote/LM Studio interoperability remain manual
acceptance checks. Tests are not a guarantee of every desktop interaction.

## 0.3.0

Initial public runtime/update tree with PubMed search, article fetching,
manual screening, reading clipboard handoff and Excel export.
