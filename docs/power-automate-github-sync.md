# Power Automate – GitHub-synkronisering for RVV Miniputt

Denne siden dokumenterer hvordan Power Automate synkroniserer godkjente
inndatafiler til GitHub-repositoriet via **issues**, slik at GitHub Actions
automatisk importerer, validerer, og publiserer de riktige offentlige sidene.

## Arkitektur

Power Automate kjører et Office Script i SharePoint som leser
``Årshjul``-arket og oppretter en maskinlesbar GitHub issue med
``content_json``. GitHub Actions validerer JSON-kontrakten, skriver
en deterministisk kanonisk fil, og kaller aktivitetskalender-publisering
direkte via ``workflow_call``.

```text
SharePoint-arbeidsbok endres
  → Office Script leser Årshjul-arket og serialiserer til JSON
  → Power Automate oppretter GitHub issue med content_json
  → GitHub Actions import-workflow validerer JSON-kontrakten
  → commit til inputs/activities/activities.json
  → workflow_call trigger aktivitetskalender-publisering
```

## Prinsipper

- **Power Automate publiserer ikke noe.** Ansvaret stopper etter at en
  maskinlesbar issue er opprettet. GitHub Actions håndterer all validering,
  import, generering og publisering.
- **Én commit per godkjent snapshot**, ikke én per skjema-respons.
- **Sammenlign innhold før commit.** Ingen commit hvis filen er uendret.
- **Stabil identifikasjon.** SharePoint-filer identifiseres med ``DriveId`` +
  ``DriveItemId``, ikke filnavn eller sti.
- **Utelat personopplysninger.** Eksporter kun de operasjonelle feltene
  repositoriet trenger.
- **Power Automate trenger kun den innebygde GitHub-koblingen** for å opprette
  issues — ingen premium HTTP actions eller PAT med skrivetilgang.
- **JSON-kontrakten er stabil.** ``content_json`` er plain JSON, ikke Base64.
  Office Scriptet skal forbli uendret med mindre det er absolutt nødvendig.

## Flyt A: Aktivitetskalender (via GitHub issues)

### Power Automate-oppsett

| Steg | Kobling | Handling |
|------|---------|----------|
| 1 | SharePoint | **When a file is created or modified (properties only)** — pek til dokumentbiblioteket. Filtrer på ``DriveId`` og ``DriveItemId`` for aktivitetsarbeidsboken. |
| 2 | Innebygd | **Delay** — 2 minutter for å la AutoSave fullføre. |
| 3 | SharePoint | **Run script** — kjør Office Scriptet som leser ``Årshjul``-arket og returnerer ``content_json``. |
| 4 | Innebygd | Bygg issue-body: se kontrakten under. ``content_json``-verdien er hele Office Script-resultatet serialisert som JSON-streng. |
| 5 | GitHub | **Create issue** — repo ``region-viken-vest-hockey/hockey``, tittel ``sharepoint-sync: activities``, body som spesifisert. |

### Issue-kontrakt

Issuen må ha **nøyaktig** denne tittelen:

```text
sharepoint-sync: activities
```

Body er én ``nøkkel=verdi`` per linje (pluss tillatte tomme/Markdown-linjer):

```text
source=sharepoint
target_path=inputs/activities/activities.xlsx
drive_id=<SharePoint DriveId>
drive_item_id=<SharePoint DriveItemId>
version=<SharePoint VersionNumber>
content_json={"schemaVersion":1,"worksheet":"Årshjul","values":[[...],...]}
```

| Felt | Påkrevd | Beskrivelse |
|------|---------|-------------|
| ``source`` | Ja | Må være ``sharepoint``. |
| ``content_json`` | Ja | Plain JSON (ikke Base64). Inneholder ``schemaVersion``, ``worksheet``, og ``values`` — en todimensjonal array med det komplette brukte området fra ``Årshjul``-arket. |
| ``target_path`` | Nei | Legacy. Kan inneholde den gamle XLSX-stien. Importøren bruker den **ikke** som skrivesti — den kanoniske destinasjonen er hardkodet. |
| ``drive_id`` | Nei | SharePoint DriveId. Brukes kun i diagnostikk. |
| ``drive_item_id`` | Nei | SharePoint DriveItemId. Brukes kun i diagnostikk. |
| ``version`` | Nei | SharePoint-versjonsnummer. Brukes kun i diagnostikk. |

Andre nøkler avvises. Duplikate nøkler avvises.

### ``content_json``-kontrakt

```json
{
  "schemaVersion": 1,
  "worksheet": "Årshjul",
  "values": [
    ["Måned", "Dato", "Aktivitet", "Aldersgruppe", "Sted"],
    ["September", 15, "Spillerutvikling U9", "U9", "Kongsberg"],
    ["Oktober", 3, "Regionsturnering U12", "U12", "Jar"]
  ]
}
```

Valideringsregler:

- Rotverdien må være et objekt.
- ``schemaVersion`` må være ``1``.
- ``worksheet`` må være ``"Årshjul"``.
- ``values`` må være en todimensjonal array.
- Hver rad må være en array.
- Celler kan kun inneholde JSON-kompatible skalarverdier (tall, tekst, bool, null) — ikke objekter eller nestede arrays.

### Hva skjer etter at issuen er opprettet

1. **GitHub Actions** ``.github/workflows/sharepoint-sync-router.yml`` trigges av
   ``issues: [opened, reopened]``.
2. Kun issues med en støttet tittel og betrodd forfatter behandles.
3. Routeren venter **5 minutter** etter siste issue. Nye issues i ventetiden
   kansellerer den forrige router-kjøringen. Etter ventetiden velges den nyeste
   åpne issuen for samme synkroniseringstype, og routeren dispatcher riktig
   import-workflow med dette issue-nummeret.
4. Import-workflowen:
   - Parser og validerer issue-body.
   - Validerer ``content_json``-kontrakten (``schemaVersion``, ``worksheet``, ``values``).
   - Serialiserer deterministisk og sammenligner SHA-256 med eksisterende kanonisk fil.
   - **Hvis endret:** committer til ``inputs/activities/activities.json``.
   - **Hvis uendret:** ingen commit.
   - Kommenterer og lukker den nyeste issuen.
   - Etter vellykket import og publisering lukkes alle eldre åpne sync-issues
     som erstattet den nyeste. Ved feil forblir issues åpne med diagnose.
5. **Ved endret commit:** workflowen kaller ``activity-publish.yml`` via
   ``workflow_call``, som regenererer og publiserer aktivitetskalenderen.
   Dette er deterministisk og avhenger ikke av at ``GITHUB_TOKEN``-pushes
   trigger nye workflows.
6. **Ved feil:** issuen forblir åpen med en diagnosekommentar og lenke til
   Actions-run. Ingen repository-filer endres.

### Håndtering av SharePoint-filer som slettes og gjenskapes

Hvis aktivitetsarbeidsboken slettes og lastes opp på nytt i SharePoint, får
den en ny `DriveItemId`. Power Automate må da:

1. Identifisere den nye filen ved hjelp av filnavn eller dokumentbibliotek-sti.
2. Oppdatere `DriveItemId`-filteret i Power Automate-flyten.
3. Opprette en ny `sharepoint-sync: activities`-issue med den nye ID-en.

Dette er en manuell operasjon — den skjer svært sjelden og dokumenteres her
for fullstendighet.

## Flyt B: Påmeldte lag

Denne flyten dekker lagsregistrering via Microsoft Forms og synkroniseres
foreløpig via direkte CSV-commit. Den kan senere migreres til samme
issue-baserte mønster som aktivitetskalenderen.

### Dagens flyt

```
Microsoft Forms
  → Power Automate-validering
  → privat SharePoint-liste
  → eksporter komplett godkjent/aktuell snapshot
  → sammenlign med eksisterende GitHub CSV
  → commit inputs/registrations/registered-teams.csv kun ved endring
  → GitHub Actions regenererer og publiserer Påmeldte lag
```

### Forhåndsvalidering i Power Automate

Før dataene når SharePoint-listen, bør Power Automate validere:

- Obligatoriske felt er fylt ut (klubb, lagsnavn, aldersgruppe).
- Aldersgruppen er en av de konfigurerte gruppene (f.eks. U7–U12, JU8–JU12).
- Ingen åpenbare duplikater (samme klubb + lagsnavn + aldersgruppe).

Godkjente svar går til en privat SharePoint-liste. Avviste svar varsles
manuelt.

### Hva skjer etter commit

GitHub Actions-workflowen `.github/workflows/registration-publish.yml` trigges
automatisk og:

1. Validerer CSV-en.
2. Synkroniserer lagdataene inn i `Lag`-arket i sesongarbeidsboken.
3. Committer oppdatert arbeidsbok med `[skip ci]` for å unngå rekursive
   workflow-kjøringer.
4. Genererer `pameldte-lag.html` og `pameldte-lag.json`.
5. Slår sammen med eksisterende `/latest/`-snapshot.
6. Publiserer til GitHub Pages.

## Samtidighet og idempotens

- **Routeren** bruker én concurrency-gruppe per synkroniseringstype og
  ``cancel-in-progress: true``. Dette er debounce-mekanismen for Power
  Automate-bursts.
- **Import-workflowene** bruker én felles concurrency-gruppe per
  synkroniseringstype, slik at eldre kjøringer ikke overskriver nyeste snapshot.
- **Publiseringsworkflowene** deler `concurrency`-gruppe
  (`routine-publish`). Dette serialiserer alle rutinepubliseringer og
  forhindrer samtidige skrivinger til `gh-pages`.
- Import-workflowen bruker `git push` og GitHub håndterer push-konflikter.
- SHA-256-sammenligning forhindrer unødvendige commits.
- Identiske inndata produserer identiske utdata.

## Eierskap og tilgang

Minimum to personer bør ha eierskap over hver komponent:

| Komponent | Minimum eiere |
|-----------|---------------|
| Power Automate-flyt (aktiviteter) | 2 klubautoriserte |
| Power Automate-flyt (påmeldinger) | 2 klubautoriserte |
| SharePoint-dokumentbibliotek | 2 klubautoriserte |
| SharePoint-liste (påmeldinger) | 2 klubautoriserte |
| GitHub repository (admin) | 2 klubautoriserte |

GitHub-koblingen i Power Automate bruker OAuth mot en klubautorisert
GitHub-konto. Ingen PAT eller hemmeligheter lagres i Power Automate-miljøet
utover den innebygde koblingen.

## Gjenoppretting

### Hvis Power Automate feiler

- **Aktivitetskalender:** Eksporter ``Årshjul``-arket manuelt fra Teams/SharePoint
  og last opp ``inputs/activities/activities.json`` via GitHub-grensesnittet
  eller ``git push``. Workflowen trigges automatisk av path-endringen.
- **Alternativt:** opprett en ny ``sharepoint-sync: activities``-issue manuelt
  med gyldig ``content_json``.
- **Påmeldte lag:** Eksporter SharePoint-listen til CSV manuelt, og last opp
  til `inputs/registrations/registered-teams.csv`.

### Hvis import-workflowen feiler

- Issuen forblir åpen med en diagnosekommentar.
- Gå til **Actions**-fanen og finn den feilede kjøringen.
- Rett feilen og opprett en ny issue med tittel `sharepoint-sync: activities`
  og oppdatert body.
- Alternativt: commit filen manuelt til `inputs/activities/activities.xlsx`.

### Manuell regenerering uten Power Automate

```bash
# Aktivitetskalender (fra JSON)
make aktivitetskalender-publish CONFIRM_PUBLIC=1 \
  ACTIVITY_INPUT=inputs/activities/activities.json

# Aktivitetskalender (fra XLSX — legacy)
make aktivitetskalender-publish CONFIRM_PUBLIC=1 \
  ACTIVITY_INPUT=inputs/activities/activities.xlsx

# Påmeldte lag
make registered-teams-publish \
  CSV=inputs/registrations/registered-teams.csv \
  CONFIRM_PUBLIC=1
```

### Hvis SharePoint-filen får ny DriveItemId

1. Identifiser den nye filens `DriveItemId` via SharePoint-grensesnittet eller
   Microsoft Graph.
2. Oppdater `DriveItemId`-filteret i Power Automate-flyten.
3. Opprett en ny `sharepoint-sync: activities`-issue for å trigge import.

## Sikkerhet

- **Ingen hemmeligheter i repositoriet.** Microsoft 365-legitimasjon,
  skjemakoder og kontaktopplysninger lagres i Power Automate/SharePoint —
  aldri i filer.
- **Offentlig påmeldingsside inneholder kun ``club``, ``label`` og ``age_group``.**
  Ingen navn, epostadresser, telefonnumre, kommentarer eller interne statuser.
- **``content_json`` skrives direkte i issue-bodyen** — ingen midlertidige
  delingslenker eller eksterne nedlastinger.
- **Power Automate har kun tilgang til å opprette issues** via den innebygde
  GitHub-koblingen — ingen repository-skrivetilgang.
- **Import-workflowen bruker ``contents: write`` og ``issues: write``** —
  minimumstillatelser for å committe filer og administrere trigger-issues.
- **Workflowene serialiseres via ``concurrency``-grupper.**
- **Midlertidige delingslenker er skrivebeskyttet (`view`)** og utløper
  automatisk.

## Relatert dokumentasjon

- [CI: required checks and branch protection](ci.md)
- [Engineering principles](engineering-principles.md)
- [Ownership and handover](ownership-and-handover.md)
- [RVV Miniputt deployment architecture](rvv-miniputt-deployment-architecture.md)
