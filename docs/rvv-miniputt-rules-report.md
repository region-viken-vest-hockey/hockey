# RVV Miniputt rules report

This is a review/discussion snapshot of the current season-planning logic.
It is based on the planner code, not on the marketing/docs wording, so it calls out where a rule is truly hard, soft, automatic, or only a warning.

## Policy vs implementation

### Policy rules

| Rule | What it does | Kind |
|---|---|---|
| Parallelle kamper for JU10: 3 | For aldersgruppen JU10 spilles det 3 kamper samtidig per runde. Det gir plass til opptil 6 lag per turnering, og hvis lagetallet er oddetall får ett lag pause i hver runde. | Hard krav |
| Parallelle kamper for JU11: 2 | For aldersgruppen JU11 spilles det 2 kamper samtidig per runde. Det gir plass til opptil 4 lag per turnering, og hvis lagetallet er oddetall får ett lag pause i hver runde. | Hard krav |
| Parallelle kamper for JU12: 2 | For aldersgruppen JU12 spilles det 2 kamper samtidig per runde. Det gir plass til opptil 4 lag per turnering, og hvis lagetallet er oddetall får ett lag pause i hver runde. | Hard krav |
| Parallelle kamper for U10: 3 | For aldersgruppen U10 spilles det 3 kamper samtidig per runde. Det gir plass til opptil 6 lag per turnering, og hvis lagetallet er oddetall får ett lag pause i hver runde. | Hard krav |
| Parallelle kamper for U11: 3 | For aldersgruppen U11 spilles det 3 kamper samtidig per runde. Det gir plass til opptil 6 lag per turnering, og hvis lagetallet er oddetall får ett lag pause i hver runde. | Hard krav |
| Parallelle kamper for U12: 2 | For aldersgruppen U12 spilles det 2 kamper samtidig per runde. Det gir plass til opptil 4 lag per turnering, og hvis lagetallet er oddetall får ett lag pause i hver runde. | Hard krav |
| Parallelle kamper for U7: 4 | For aldersgruppen U7 spilles det 4 kamper samtidig per runde. Det gir plass til opptil 8 lag per turnering, og hvis lagetallet er oddetall får ett lag pause i hver runde. | Hard krav |
| Parallelle kamper for U8: 4 | For aldersgruppen U8 spilles det 4 kamper samtidig per runde. Det gir plass til opptil 8 lag per turnering, og hvis lagetallet er oddetall får ett lag pause i hver runde. | Hard krav |
| Parallelle kamper for U9: 3 | For aldersgruppen U9 spilles det 3 kamper samtidig per runde. Det gir plass til opptil 6 lag per turnering, og hvis lagetallet er oddetall får ett lag pause i hver runde. | Hard krav |
| Sesongen deles i to uavhengige planleggingshalvdeler (før/etter jul) | Nyttårsskillet (Før jul/Etter jul) beregnes én gang som `christmas_split_date` (1. januar) i planleggingsproblemet og deles av verify_candidate, score_candidate og Stage 3-søket. En turnering kan flyttes til en ny dato innenfor sin egen halvdel, men flytting over nyttårsskillet krever et eksplisitt `allow_cross_half_moves`-unntak og skjer aldri som en bieffekt av optimaliseringssøket. | Hard krav |
| Deltakelsesmål per aldersgruppe kan splittes før/etter jul | Når en aldersgruppe har et eksplisitt mål, kan det settes som `before_christmas` / `after_christmas`. Planleggeren fordeler da målet over sesongen rundt juleskillet i stedet for å behandle hele sesongen som ett samlet mål. | Hard krav |
| U- og JU-kategorier blandes aldri i samme turnering | Aldersgruppen er en eksakt kategori-streng (f.eks. «U10» eller «JU10»), aldri bare det numeriske alderstallet. Et lags aldersgruppe må være identisk med turneringens aldersgruppe for at laget kan delta -- «U10» og «JU10» er alltid to forskjellige turneringer, selv om de deler samme tall. Dette gjelder likt for grunnplanen, lokalsøket og CP-SAT-søket, og verify_candidate() avviser enhver kandidat med et `age_group_mismatch`-avvik uansett hvilken motor som produserte den. `AGE_GROUP_OVERLAP` er en egen regel om datokollisjon mellom nærliggende aldersgrupper og gir aldri en klubb lov til å sette sammen U- og JU-lag i én turnering. | Hard krav |

### Configuration and guardrails

| Rule | What it does | Kind |
|---|---|---|
| Konfigurasjonsstandarder og fairness-terskler | Deltakelsesmål for hvert lag er de autoritative før/etter-jul-verdiene per aldersgruppe i `Aldersgrupper` (`deltakelser_per_lag_før_jul` / `_etter_jul`) — et globalt `deltakelser_per_lag` / `target_tournament_count` i `Innstillinger` støttes ikke lenger i kanonisk input.xlsx. Klubb-taket per turnering er flatt 1 lag per klubb (issue #324: ikke lenger skalert opp av klubbstørrelse eller deficit-spredning). Fairness-terskler som brukes av fairness-gaten: max_game_count_spread=2, max_hosting_deviation=1, max_same_weekend_club_load=3, max_team_travel_km=4000 (sesongtotal reisebelastning), min_diversity_score=0.75, min_month_balance_score=0.75, min_pairwise_matchup_score=0.25, max_consecutive_weekend_club_load=2, max_holiday_stretch_club_load=2, max_team_temporal_gap_weeks=8.0. | Konfigurasjonsstandard |
| Maks vertskapsdager per måned | `max_hosting_days_per_month` kan settes i `Innstillinger` og sendes til Stage 3. Når verdien er positiv, unngår planleggeren nye vertsdager i en måned der kandidatklubben allerede har nådd grensen. | Mykt planleggingskrav |
| Standard starttid: 10:00 | Når en turnering ikke får et mer spesifikt slot-forslag fra hallkalenderen, brukes 10:00 som standard starttid. Dette er hovedsakelig et teknisk utgangspunkt for planlegging og visning. | Konfigurasjonsstandard |
| Buffer mellom turneringer i samme hall-dag: 5 min | Når flere turneringer havner i samme arena samme dag, legges det inn 5 minutter buffer mellom starttidene. Hvis sekvensen ikke får plass innen siste gyldige starttid, registreres dette som en hard arenakollisjon i stedet for å klemme starttiden tilbake. Turneringen havner da i «Må planlegges manuelt»-visningen for manuell istidsplanlegging. | Konfigurasjonsstandard |

### Implementation rules

| Rule | What it does |
|---|---|
| Per-klubb kapasitet er et flatt, foretrukket tak | Planleggeren foretrekker maks 1 lag per klubb i én turnering, uavhengig av hvor mange lag klubben har i aldersgruppen (issue #324). Dette er en sterk poengsettingsstraff, ikke et hardt forbud -- et tredje eller senere lag fra samme klubb kan fortsatt velges når ingen andre lovlige kandidater kan fylle turneringen. |
| Age-group-aware hosting | Hjemmeturneringer fordeles per aldersgruppe, ikke bare globalt. Planleggeren forsøker å gi hver klubb en andel hjemmeturneringer som matcher hvor mange lag klubben har i den aktuelle aldersgruppen, og samme klubb kan ikke få uendelig mange helger på rad i samme aldersgruppe. Det proporsjonale målet har et dekningsgulv på minst 1 hjemmeturnering per klubb x aldersgruppe (når antall turneringer i aldersgruppen tillater det) — et lag skal aldri "arve" en klubbs mangel på vertskap fordi klubben allerede fyller sin andel i en annen aldersgruppe (issue #266). |
| Klubb x aldersgruppe-dekning og manuell reserveløsning (issue #266) | Hver klubb med minst ett registrert lag i en aldersgruppe skal ha minst én hjemmeturnering i akkurat den aldersgruppen. Dersom planleggeren, etter å ha forsøkt hele tilgjengelighetshierarkiet (faste tildelinger, bekreftet ledig tid, klubbstyrte tildelingsvinduer, operatørbekreftede overstyringer), ikke finner noen godkjent ledig arenatid for kravet, holdes kravet uløst i stedet for at en annen klubb stilles som vert og kravet regnes som oppfylt. Uløste krav havner i «Må planlegges manuelt»-visningen (manual_schedule.html) som et eksplisitt «MANUAL PLACEMENT REQUIRED»-punkt med klubb, aldersgruppe og årsak. |
| Vertsklubben skal være representert i egen turnering | Når vertsklubben har et registrert lag i turneringens aldersgruppe, må minst ett deltakende lag representere den klubben -- en hjemmeturnering uten et lag fra vertsklubben er ikke operativt gyldig. Dette er et hardt korrekthetskrav, ikke en rettferdighetspreferanse: grunnplanen reparerer deltakerlisten når verten mangler, lokalsøket (Stage 3) og CP-SAT kan aldri fjerne eller bytte bort den siste representanten, og den uavhengige verifikasjonen avviser enhver kandidat der kravet brytes, uansett hvilken motor som produserte den. Felles/delt klubbregistrering (f.eks. «Jutul/Jar») teller som representasjon for begge de fysiske klubbene registreringen består av. |
| Felles klubbregistrering telles pr. vertsklubb, ikke pr. registreringsstreng (issue #274) | En registrering som «Kongsberg/Tønsberg» dekker sin egen klubb x aldersgruppe-rad, men når planleggeren faktisk plasserer en turnering blir verten alltid én fysisk klubb. Dekningskravet for den felles raden regnes derfor som oppfylt så snart *en av* deltakerklubbene er vert for aldersgruppen -- ikke bare ved et eksakt strengtreff på registreringsnavnet. |
| Kalendertillit er skilt fra vellykket skraping (issue #274) | En klubbs kalenderstatus kan være «known» (nok bevis for automatisk plassering), «unknown» (blokkert/hoppet over/feilet skraping), eller «untrusted» (skrapingen lyktes, men klubbens registeroppføring sier at dataene ikke er den reelle, fullstendige kalenderen -- for eksempel Tønsbergs BookUp-kilde, som i dag kun returnerer generiske/offentlige plassholderdata). En klubb med «untrusted» eller «unknown» status beholder sin fulle andel av vertskapsansvaret, men enhver turnering den er vertskap for må planlegges manuelt inntil et fullstendig, autentisert kalendersøk er bevist pålitelig -- ingen spesialtilfelle-logikk i planleggeren kreves for å oppgradere klubben senere. |
| Delt vertskap for felles klubbregistreringer avgjøres av LLM (issue #274) | Hvilken av deltakerklubbene i en felles registrering som skal bære et konkret vertskapsansvar er en kontekstuell rettferdighetsvurdering. Python eksponerer kun deterministiske fakta (antall vertskap per klubb, kalendertillit per klubb, om automatisk plassering er mulig) via `hosting_coverage.shared_registration_facts`; en LLM/kontroller velger blant registreringens egne deltakerklubber (`shared_host_decision.py`), og valget valideres deterministisk mot nettopp disse klubbene. Kalenderutilgjengelighet avgjør aldri valget alene -- hvis den valgte klubben mangler en pålitelig automatisk ledig tid, blir turneringen manuell for akkurat den klubben i stedet for at ansvaret stille flyttes til den andre. |
| Verifikatorens uløste vertskapskrav er autoritative ved eksport (issue #274) | Rett før Stage 4-eksport kjøres den endelige uavhengige verifikasjonen på den endelige planen på nytt, og resultatet overskriver planens `unresolved_hosting_obligations`/`unresolved_external_conflicts`/`unresolved_participation_shortfalls` før `manual_schedule.html` rendres. Verifikasjonen klassifiserer også planen som `INVALID`, `REVIEW_REQUIRED` eller `PUBLISHABLE`. |
| Weekend- og feriehelgelast balanseres | Vertskapsvalg foretrekker klubber som ikke allerede ligger i en lang helgestripe, og ferie-/helligdagshelger telles separat. Dette er en myk balanse- og fairness-regel, ikke et absolutt forbud. |
| Minst mulig gjentatte grupperinger | Når planleggeren velger hvilke lag som skal møtes i en turnering, regnes det ut én samlet score for hver kandidat. Scoren balanserer klubb-tak, game-count-deficit og gjentatte motstandere, slik at lag som både trenger flere kamper og passer inn i turneringen prioriteres først. |
| Sesongvinduet planlegges med global utjevning | Sesongvinduet deles i omtrent like store tidsbolker, og planleggeren gjør i tillegg en global best-first-utjevning over hele sesongen før datoene låses. Det gjør at månedslast, overlappende aldersgrupper og gjentatte matchups kan rebalanseres på tvers av grupper, i stedet for at hver aldersgruppe bare følger sin egen lokale bucket. Måneder som avviker mer enn 50% fra forventet antall turneringer flagges som et varsel. |
| Repair-/backtracking-pass etter første forslag | Etter at en tentativ dato-plan er bygd, kjøres en liten hill-climbing reparasjonsrunde som kan bytte ut tidligere valg dersom en senere kollisjon eller repeterende score gir en bedre helhet. Planleggeren gjør altså ikke bare én greedy pass. |
| Jevnt antall kamper per lag | Planleggeren teller opp alle kamper hvert lag spiller i løpet av sesongen. Forskjellen mellom laget med flest og færrest kamper skal være maksimalt 2. Sesongdekningen vurderes samlet langs hele kjeden sesongstart -> første turnering -> ... -> siste turnering -> sesongslutt: lag med et opphold (før første turnering, mellom to turneringer, eller etter siste turnering) på mer enn 60 dager flagges som et varsel, ikke bare lag som blir ferdige for tidlig. |
| Jevn fordeling av kamper innad i aldersgruppe/klubb | For hver aldersgruppe beregnes gjennomsnittlig antall kamper per lag. Lag som avviker fra dette gjennomsnittet med mer enn 2 kamper flagges som et varsel. Dette fanger opp skjevheter der en klubb med flere lag i samme aldersgruppe får færre eller flere kamper enn andre lag i samme aldersgruppe. |
| Lag skal ha jevn sesongdekning fra sesongstart til sesongslutt | For hvert lag måles det største gapet langs hele kjeden sesongstart -> første turnering -> ... -> siste turnering -> sesongslutt (ikke bare avstand til andre lag i aldersgruppen, og ikke bare opphold mellom eksisterende turneringer). Standardterskelen er 8 uker — satt over den vanlige jule-/nyttårspausen, slik at selve pausen ikke i seg selv flagges. Fanger opp både lag som treffer kampmål-tallet men spiller alt tidlig og "forsvinner" fra sesongen (stort gap til sesongslutt), lag som starter sent (stort gap fra sesongstart), og et hull midt i sesongen. Alle lag som overstiger terskelen listes, ikke bare det verste. Myk kvalitetsmetrikk i fairness-gaten (`team_temporal_coverage`), ikke et hardt krav. |
| Behovsbasert unntak fra klubb-tak per turnering | Når et lag fra en klubb som allerede har fylt det foretrukne klubb-taket (_max_club_teams_for, flatt 1 lag) er den eneste gjenværende måten å fylle turneringen lovlig på, kan laget likevel velges — med en sterk straff i prioriteringen proporsjonal med hvor langt over taket klubben er (issue #324). Dette unntaket er brukt 0 gang(er) i denne sesongplanen. |
| Ingen overlappende aldersgrupper | Aldersgrupper som deler spillerbase (for eksempel JU11 og U10) skal helst ikke ha turnering samme helg, fordi noen spillere tilhører begge grupper og ville blitt dobbeltbooket. Planleggeren forsøker å unngå dette; kollisjoner som ikke kan løses, rapporteres. |
| Ingen overlappende arenaintervaller | Hver turnering reserverer et fullt dato-/tidsintervall i arenaen, inkludert setup-/byttebuffer per runde og intervaller som går over midnatt. Senere plasseringer sjekkes mot både skrapede hallbookinger og turneringer som allerede er lagt inn i planen. En faktisk overlappende intervallkollisjon er et hardt avvik: den endelige uavhengige verifikasjonen blokkerer normal strict produksjonseksport til kollisjonen er løst. Kollisjonen listes også i «Må planlegges manuelt»-visningen (manual_schedule.html). |
| Klubber uten skrapet kalender får sin andel hjemmeturneringer — merket for manuell istidsbooking | Når en klubbs kalenderkilde ikke kan skrapes (blokkert/feilet), får klubben likevel sin forholdsmessige andel hjemmeturneringer i planen. Siden starttiden ikke kan verifiseres mot den ekte ishall-kalenderen, merkes disse turneringene (manual_booking_reason) og listes i «Må planlegges manuelt»-visningen (manual_schedule.html) — istiden må bookes/verifiseres for hånd. Klubben beholdes i hjemmebanebelastningsberegningen og teller fullt ut i selve planen. |
| Round-robin: alle mot alle innen turneringen | Innenfor hver turnering spiller alle inviterte lag mot hverandre nøyaktig én gang (round-robin). Turneringens størrelse og antall parallelle kamper avgjør hvor mange runder som trengs. Hjemme/borte byttes annenhver runde for rettferdig fordeling. |
| Klubb-interne kamper følger round-robin | Hvis flere lag fra samme klubb deltar i samme turnering, behandles de som øvrige deltakere: round-robin-genereringen lager én kamp mellom hvert inviterte lagpar, også mellom lag fra samme klubb. Deltakerutvelgelsen forsøker å spre klubber, men dette er et mykt hensyn og ikke et kampfilter. |

### Warnings / diagnostics

| Rule | What it does | Kind |
|---|---|---|
| Klubbbelastning per turnering | Kjører en advarsel når en klubb har flere lag i en turnering enn det effektive taket tillater; 0 tilfelle(r) er registrert i denne planen. | Advarsel |
| Hjemmeturneringsfordeling | Kjører en advarsel når en klubb avviker for mye fra proporsjonal hjemmeturneringsfordeling per aldersgruppe; 0 tilfelle(r) er registrert i denne planen. | Advarsel |
| Helgebelastning og feriehelger | Kjører en advarsel når en klubb får for mange sammenhengende vertskapshelger eller for mange ferie-/helligdagshelger; 0 tilfelle(r) er registrert i denne planen. | Advarsel |
| Kampbalanse og sesongdekning | Kjører en advarsel når kampantall spres for mye mellom lag eller når et lag har et opphold i sesongdekningen (sesongstart -> første turnering -> ... -> siste turnering -> sesongslutt) som overstiger terskelen; 0 tilfelle(r) er registrert i denne planen. | Advarsel |
| Skjev kampfordeling per aldersgruppe | Kjører en advarsel når et lag avviker for mye fra aldersgruppens forventede kampmengde; 0 tilfelle(r) er registrert i denne planen. | Advarsel |
| Feasibility / kapasitet | Kjører en advarsel når sesongvinduet sannsynligvis ikke kan oppfylle deltakelsesmålet; 0 tilfelle(r) er registrert i denne planen. | Advarsel |
| Arena-intervallkollisjoner | Kjører en hard validering av fullstendige start-/sluttintervaller i samme arena, ikke bare datoantall; 0 tilfelle(r) er registrert i denne planen. En faktisk kollisjon blokkerer normal strict produksjonseksport og listes samtidig i «Må planlegges manuelt»-visningen (manual_schedule.html). | Hard krav |
| Fallback vertsklubb | Kjører en advarsel når planleggeren må beholde opprinnelig vertsklubb fordi ønsket slot ikke finnes; 0 tilfelle(r) er registrert i denne planen. | Advarsel |
| Månedslast | Kjører en advarsel når en måned avviker mer enn terskelen fra forventet turneringslast; 0 tilfelle(r) er registrert i denne planen. | Advarsel |
| Mistenkelig få kalenderhendelser i Stage 2 | Før planlegging sammenlignes hver skrapet kilde mot et grovt forventet minimum utledet fra datoperiode og aktive aldersgrupper. Kilder med hendelser, men uvanlig lavt antall, får `event_expectation.status=low` og listes i `event_expectation_warnings`. Dette er en recovery-prioritet, ikke et hardt stopp. | Advarsel |

## Final verification and publication readiness

The search-time planning contract keeps `ok` narrowly defined as structural validity so planners and optimizers can compare candidates without turning every unresolved operational task into a hard solver failure. Immediately before production export, the stricter final verifier additionally checks that production tournaments are not below the three-team minimum and that each tournament's game list is a complete, non-duplicated round robin with no team scheduled twice in one round.

The final verifier publishes a separate readiness state:

- `INVALID`: at least one hard structural/integrity violation exists.
- `REVIEW_REQUIRED`: structurally valid, but unresolved hosting, calendar placement, external calendar conflict, participation shortfall, or incomplete verification remains.
- `PUBLISHABLE`: hard verification passed and none of those unresolved obligations remains.

This classification is evidence for the operator/publication decision; it does not weaken the existing explicit human confirmation required to publish.

## Important discussion point

The per-club "minimum 1" value is `max_club_teams_per_tournament`'s configured default; production runs set it to 2 (issue #324). It is now a flat preferred ceiling, not a floor scaled up by club size or fairness-deficit spread.

## Primary source files

- `tournament_scheduler/rules_report.py`
- `tournament_scheduler/final_verification.py`
- `tournament_scheduler/planning_contract.py`
- `tournament_scheduler/arena_conflicts.py`
- `tournament_scheduler/participant_selection.py`
- `tournament_scheduler/warnings.py`
- `tournament_scheduler/host_assignment.py`
- `tournament_scheduler/season_planner.py`
- `tournament_scheduler/game_generation.py`
- `tournament_scheduler/models.py`
- `tournament_scheduler/season_config.py`