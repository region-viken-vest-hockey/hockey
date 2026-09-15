# RVV Miniputt rules report

This is a review/discussion snapshot of the current season-planning logic.
It is based on the planner code, not on the marketing/docs wording, so it calls out where a rule is truly hard, soft, automatic, or only a warning.

## Policy vs implementation

### Policy rules

| Rule | What it does | Kind |
|---|---|---|
| Parallelle kamper for JU10: 3 | For aldersgruppen JU10 spilles det 3 kamper samtidig per runde. Det gir plass til opptil 6 lag per turnering. Runder per turnering: full serie. Når et begrenset rundetall er satt, genereres kampene direkte for dette antallet runder og unngår interne klubboppgjør når det er mulig. | Hard krav |
| Parallelle kamper for JU12: 2 | For aldersgruppen JU12 spilles det 2 kamper samtidig per runde. Det gir plass til opptil 4 lag per turnering. Runder per turnering: full serie. Når et begrenset rundetall er satt, genereres kampene direkte for dette antallet runder og unngår interne klubboppgjør når det er mulig. Når den registrerte lagpoolen for JU12 kan fylle det, er dagens foretrukne turneringsstørrelse nøyaktig 4 lag (3 runder, 6 kamper totalt per turnering). En turnering som velger færre lag enn det den fulle registrerte poolen tillater er alltid ugyldig. | Hard krav |
| Parallelle kamper for JU8: 4 | For aldersgruppen JU8 spilles det 4 kamper samtidig per runde. Det gir plass til opptil 8 lag per turnering. Runder per turnering: full serie. Når et begrenset rundetall er satt, genereres kampene direkte for dette antallet runder og unngår interne klubboppgjør når det er mulig. | Hard krav |
| Parallelle kamper for U10: 3 | For aldersgruppen U10 spilles det 3 kamper samtidig per runde. Det gir plass til opptil 6 lag per turnering. Runder per turnering: full serie. Når et begrenset rundetall er satt, genereres kampene direkte for dette antallet runder og unngår interne klubboppgjør når det er mulig. | Hard krav |
| Parallelle kamper for U11: 3 | For aldersgruppen U11 spilles det 3 kamper samtidig per runde. Det gir plass til opptil 6 lag per turnering. Runder per turnering: full serie. Når et begrenset rundetall er satt, genereres kampene direkte for dette antallet runder og unngår interne klubboppgjør når det er mulig. | Hard krav |
| Parallelle kamper for U12: 2 | For aldersgruppen U12 spilles det 2 kamper samtidig per runde. Det gir plass til opptil 4 lag per turnering. Runder per turnering: full serie. Når et begrenset rundetall er satt, genereres kampene direkte for dette antallet runder og unngår interne klubboppgjør når det er mulig. Når den registrerte lagpoolen for U12 kan fylle det, er dagens foretrukne turneringsstørrelse nøyaktig 4 lag (3 runder, 6 kamper totalt per turnering). En turnering som velger færre lag enn det den fulle registrerte poolen tillater er alltid ugyldig. | Hard krav |
| Parallelle kamper for U7: 4 | For aldersgruppen U7 spilles det 4 kamper samtidig per runde. Det gir plass til opptil 8 lag per turnering. Runder per turnering: full serie. Når et begrenset rundetall er satt, genereres kampene direkte for dette antallet runder og unngår interne klubboppgjør når det er mulig. | Hard krav |
| Parallelle kamper for U8: 4 | For aldersgruppen U8 spilles det 4 kamper samtidig per runde. Det gir plass til opptil 8 lag per turnering. Runder per turnering: full serie. Når et begrenset rundetall er satt, genereres kampene direkte for dette antallet runder og unngår interne klubboppgjør når det er mulig. | Hard krav |
| Parallelle kamper for U9: 4 | For aldersgruppen U9 spilles det 4 kamper samtidig per runde. Det gir plass til opptil 8 lag per turnering. Runder per turnering: full serie. Når et begrenset rundetall er satt, genereres kampene direkte for dette antallet runder og unngår interne klubboppgjør når det er mulig. | Hard krav |
| Sesongen deles i to uavhengige planleggingshalvdeler (før/etter jul) | Nyttårsskillet (Før jul/Etter jul) beregnes én gang som `christmas_split_date` (1. januar) i planleggingsproblemet og deles av verify_candidate, score_candidate og Stage 3-søket. En turnering kan flyttes til en ny dato innenfor sin egen halvdel, men flytting over nyttårsskillet krever et eksplisitt `allow_cross_half_moves`-unntak og skjer aldri som en bieffekt av optimaliseringssøket. | Hard krav |
| U- og JU-kategorier blandes aldri i samme turnering | Aldersgruppen er en eksakt kategori-streng (f.eks. «U10» eller «JU10»), aldri bare det numeriske alderstallet. Et lags aldersgruppe må være identisk med turneringens aldersgruppe for at laget kan delta -- «U10» og «JU10» er alltid to forskjellige turneringer, selv om de deler samme tall. Dette gjelder likt for grunnplanen, lokalsøket og CP-SAT-søket, og verify_candidate() avviser enhver kandidat med et `age_group_mismatch`-avvik uansett hvilken motor som produserte den. `AGE_GROUP_OVERLAP` er en egen regel om datokollisjon mellom nærliggende aldersgrupper og gir aldri en klubb lov til å sette sammen U- og JU-lag i én turnering. | Hard krav |
| Ingen overlappende arenaintervaller | Hver turnering reserverer et fullt dato-/tidsintervall i arenaen, inkludert setup-/byttebuffer per runde og intervaller som går over midnatt. Senere plasseringer sjekkes mot både skrapede hallbookinger og turneringer som allerede er lagt inn i planen. Enhver faktisk intervallkollisjon i samme arena er et hardt avvik: den endelige uavhengige verifikasjonen blokkerer normal strict produksjonseksport. Kollisjonen listes også i «Må planlegges manuelt»-visningen (manual_schedule.html) for operatøroppfølging. | Hard krav |
| Klubb x aldersgruppe-dekning av hjemmeturneringer (issue #266) | Hver klubb med minst ett registrert lag i en aldersgruppe skal ha minst én hjemmeturnering i akkurat den aldersgruppen denne sesongen -- ekstra hjemmeturneringer i en annen aldersgruppe teller ikke. Når planleggeren ikke finner en godkjent ledig arenatid for et slikt krav, holdes kravet uløst i stedet for å stille en annen klubb som vert i stedet, og legges i «Må planlegges manuelt»-visningen (manual_schedule.html) som «MANUAL PLACEMENT REQUIRED». 0 uløst(e) krav er registrert i denne planen. | Hard krav |
| Vertsklubben skal være representert i egen turnering | Når vertsklubben har et registrert lag i aldersgruppen, må minst ett deltakende lag representere den -- et hardt korrekthetskrav. Delt/felles registrering teller for begge vertsklubbene den består av. Den endelige verifikasjonen avviser enhver kandidat der kravet brytes. | Hard krav |
| Kalendertillit er skilt fra vellykket skraping (issue #274) | En klubbs kalenderstatus kan være «known» (nok bevis for automatisk plassering), «unknown» (blokkert/hoppet over/feilet skraping), eller «untrusted» (skrapingen lyktes, men klubbens registeroppføring sier at dataene ikke er den reelle, fullstendige kalenderen -- for eksempel Tønsbergs BookUp-kilde, som i dag kun returnerer generiske/offentlige plassholderdata). En klubb med «untrusted» eller «unknown» status beholder sin fulle andel av vertskapsansvaret, men enhver turnering den er vertskap for må planlegges manuelt inntil et fullstendig, autentisert kalendersøk er bevist pålitelig. | Hard krav |
| Delt vertskap for felles klubbregistreringer avgjøres av LLM (issue #274) | En registrering som «Kongsberg/Tønsberg» har mer enn én gyldig fysisk vert. Hvilken av de to klubbene som skal bære et konkret vertskapsansvar for en aldersgruppe er en kontekstuell rettferdighetsvurdering -- Python eksponerer kun de deterministiske fakta (antall vertskap per klubb, kalendertillit per klubb, om automatisk plassering er mulig), og en LLM/kontroller velger blant registreringens egne deltakerklubber. Kalenderutilgjengelighet kan aldri i seg selv avgjøre valget; hvis den valgte klubben mangler en pålitelig automatisk ledig tid, blir turneringen manuell for akkurat den klubben i stedet for at ansvaret stille overføres til den andre. | Hard krav |
| Vertskap velges blant deltakerne, ikke omvendt (issue #323) | Vertskap/arena for en turnering velges bare blant klubbene til turneringens egne deltakere -- deltakerne velges først, deretter avledes de lovlige vertsklubbene fra dem. Når ingen av deltakernes klubber har en lovlig ledig arena-/tidsluke, blir turneringen ikke automatisk plassert hos en urelatert klubb, og deltakerlisten endres ikke for å passe en urelatert arena -- den legges i «Må planlegges manuelt»-visningen (manual_schedule.html) som «MANUAL PLACEMENT REQUIRED». 0 slik(e) uløst(e) plassering(er) er registrert i denne planen. | Hard krav |
| Arena-intervallkollisjoner | Kjører en hard validering av fullstendige start-/sluttintervaller i samme arena, ikke bare datoantall; 0 tilfelle(r) er registrert i denne planen. En faktisk kollisjon blokkerer normal strict produksjonseksport og listes samtidig i «Må planlegges manuelt»-visningen (manual_schedule.html). | Hard krav |

### Configuration and guardrails

| Rule | What it does | Kind |
|---|---|---|
| Konfigurasjonsstandarder og fairness-terskler | Deltakelsesmål utledes fra lagmønsteret og kapasiteten når de ikke er satt eksplisitt, og klubb-taket per turnering er flatt 1 lag per klubb. Fairness-terskler som brukes av fairness-gaten: max_consecutive_weekend_club_load=2, max_game_count_spread=2, max_holiday_stretch_club_load=2, max_hosting_deviation=1, max_same_weekend_club_load=3, max_team_temporal_gap_weeks=8.0, max_team_travel_km=4000, min_diversity_score=0.75, min_month_balance_score=0.75, min_pairwise_matchup_score=0.25. | Konfigurasjonsstandard |
| Standard starttid: 10:00 | Når en turnering ikke får et mer spesifikt slot-forslag fra hallkalenderen, brukes 10:00 som standard starttid. Dette er hovedsakelig et teknisk utgangspunkt for planlegging og visning. | Konfigurasjonsstandard |
| Buffer mellom turneringer i samme hall-dag: 5 min | Når flere turneringer havner i samme arena samme dag, legges det inn 5 minutter buffer mellom starttidene. Dersom sekvensen ikke får plass innen siste gyldige starttid, registreres dette som en hard arenakollisjon i stedet for å klemme starttiden tilbake. Turneringen havner da i «Må planlegges manuelt»-visningen (manual_schedule.html) for manuell istidsplanlegging. | Konfigurasjonsstandard |

### Implementation rules

| Rule | What it does | Kind |
|---|---|---|
| Per-klubb kapasitet er et flatt, foretrukket tak | Planleggeren foretrekker maks 1 lag per klubb i én turnering, uavhengig av hvor mange lag klubben har i aldersgruppen (issue #324). Dette er en sterk poengsettingsstraff, ikke et hardt forbud -- et tredje eller senere lag fra samme klubb kan fortsatt velges når ingen andre lovlige kandidater kan fylle turneringen. | Automatisk avgjørelse |
| Minst mulig gjentatte grupperinger | Når planleggeren velger hvilke lag som skal møtes i en turnering, regnes det ut én samlet score for hver kandidat. Scoren balanserer klubb-tak, game-count-deficit og gjentatte motstandere, slik at lag som både trenger flere kamper og passer inn i turneringen prioriteres først. | Automatisk avgjørelse |
| Jevn fordeling av turneringer over sesongen | Sesongvinduet deles i omtrent like store tidsbolker, og planleggeren gjør i tillegg en global utjevningspass over hele sesongen før datoene låses. Det gjør at månedslast, overlappende aldersgrupper og gjentatte matchups kan rebalanseres på tvers av grupper, i stedet for at hver aldersgruppe bare følger sin egen lokale bucket. Måneder som avviker mer enn 50% fra forventet antall turneringer flagges som et varsel. | Automatisk avgjørelse |
| Rettferdig fordeling av hjemmeturneringer | Hjemmeturneringer fordeles proporsjonalt etter antall lag hver klubb stiller. Klubber med flere lag får hjemmeturnering oftere. Maksimalt tillatt avvik fra forventet antall er 1 turnering(er). | Automatisk avgjørelse |
| Helgebelastning og feriehelger | Når flere klubber er aktuelle som vertskap på samme dato, foretrekker planleggeren klubber som ikke har hatt vertskap på forrige helg, og som har færre ferie-/helligdagshelger allerede. Fairness-gaten kan slå ut når samme klubb får mer enn 2 sammenhengende vertskapshelger eller mer enn 2 ferie-/helligdagshelger. | Automatisk avgjørelse |
| Jevnt antall kamper per lag | Planleggeren teller opp alle kamper hvert lag spiller i løpet av sesongen. Forskjellen mellom laget med flest og færrest kamper skal være maksimalt 2. Sesongdekningen vurderes samlet langs hele kjeden sesongstart -> første turnering -> ... -> siste turnering -> sesongslutt: lag med et opphold (før første turnering, mellom to turneringer, eller etter siste turnering) på mer enn 60 dager flagges som et varsel, ikke bare lag som blir ferdige for tidlig. | Automatisk avgjørelse |
| Jevn fordeling av kamper innad i aldersgruppe/klubb | For hver aldersgruppe beregnes gjennomsnittlig antall kamper per lag. Lag som avviker fra dette gjennomsnittet med mer enn 2 kamper flagges som et varsel. Dette fanger opp skjevheter der en klubb med flere lag i samme aldersgruppe får færre eller flere kamper enn andre lag i samme aldersgruppe. | Automatisk avgjørelse |
| Behovsbasert unntak fra klubb-tak per turnering | Når et lag fra en klubb som allerede har fylt det foretrukne klubb-taket (_max_club_teams_for, flatt 1 lag) er den eneste gjenværende måten å fylle turneringen lovlig på, kan laget likevel velges — med en sterk straff i prioriteringen proporsjonal med hvor langt over taket klubben er (issue #324). Dette unntaket er brukt 0 gang(er) i denne sesongplanen. | Automatisk avgjørelse |
| Ingen overlappende aldersgrupper | Aldersgrupper som deler spillerbase (for eksempel JU11 og U10) skal helst ikke ha turnering samme helg, fordi noen spillere tilhører begge grupper og ville blitt dobbeltbooket. Planleggeren forsøker å unngå dette; kollisjoner som ikke kan løses, rapporteres. | Automatisk avgjørelse |
| Klubber uten skrapet kalender får sin andel hjemmeturneringer — merket for manuell istidsbooking | Når en klubbs kalenderkilde ikke kan skrapes (blokkert/feilet), får klubben likevel sin forholdsmessige andel hjemmeturneringer i planen. Siden starttiden ikke kan verifiseres mot den ekte ishall-kalenderen, merkes disse turneringene (manual_booking_reason) og listes i «Må planlegges manuelt»-visningen (manual_schedule.html) — istiden må bookes/verifiseres for hånd. Klubben beholdes i hjemmebanebelastningsberegningen og teller fullt ut i selve planen. | Automatisk avgjørelse |
| Round-robin: alle mot alle innen turneringen | Innenfor hver turnering spiller alle inviterte lag mot hverandre nøyaktig én gang (round-robin). Turneringens størrelse og antall parallelle kamper avgjør hvor mange runder som trengs. Hjemme/borte byttes annenhver runde for rettferdig fordeling. | Automatisk avgjørelse |
| Klubb-interne kamper følger round-robin | Hvis flere lag fra samme klubb deltar i samme turnering, behandles de som øvrige deltakere: round-robin-genereringen lager én kamp mellom hvert inviterte lagpar, også mellom lag fra samme klubb. Deltakerutvelgelsen forsøker å spre klubber, men dette er et mykt hensyn og ikke et kampfilter. | Automatisk avgjørelse |
| Ekstern kalenderkonflikt håndteres ikke-blokkerende | Når en turnerings vertskap har en reell kollisjon med en kjent ekstern kalenderbooking som verken planleggeren eller optimeringen klarte å unngå innenfor søkebudsjettet, avvises ikke hele planen -- konflikten legges i «Må planlegges manuelt»-visningen (manual_schedule.html) som «MANUAL PLACEMENT REQUIRED». 0 slik(e) konflikt(er) er registrert i denne planen. | Automatisk avgjørelse |
| Avvik fra måltall for deltakelse håndteres ikke-blokkerende | Når et lags faktiske antall turneringer avviker fra måltallet (som oftest fordi det ikke fantes nok ledige turneringsplasser denne sesongen), avvises ikke hele planen -- avviket er en planleggingskvalitet-avvik, ikke en istidsoppgave, og rapporteres derfor her i sesongrapporten i stedet for i «Må planlegges manuelt»-visningen (manual_schedule.html). 0 slik(e) avvik er registrert i denne planen. | Automatisk avgjørelse |
| Deltakelsesmål per aldersgruppe (før/etter jul) | Hver aktiv aldersgruppe har et konfigurert mål for antall turneringsdeltakelser per lag, uavhengig før og etter nyttår: JU10: 4 før jul / 4 etter jul, JU12: 5 før jul / 5 etter jul, JU8: 3 før jul / 3 etter jul, U10: 4 før jul / 4 etter jul, U11: 5 før jul / 5 etter jul, U12: 6 før jul / 6 etter jul, U7: 0 før jul / 3 etter jul, U8: 3 før jul / 3 etter jul, U9: 4 før jul / 4 etter jul. Dette er det autoritative målet per lag og halvsesong — planleggeren, CP-SAT-optimaliseringen og verifikatoren bruker det direkte, ikke som en vekt for å fordele et sesongtotalt turneringsantall. | Automatisk avgjørelse |
| Tidspunkt på dagen velges ut fra vertsklubbens egen hallkalender | For hver turnering beregnes hvor lang tid hele turneringen tar (rundelengde × antall runder pluss buffer), og planleggeren ser etter en sammenhengende ledig luke av denne lengden i vertsklubbens egen hallkalender og i planens egne reservasjoner. Tidspunkt nærmest 11:00 foretrekkes, for å unngå svært tidlige eller sene starttider. Hvis den opprinnelige vertsklubben ikke har en passende ledig luke, prøver planleggeren andre klubber med ledig kapasitet på samme dato; hvis ingen kandidat passer, registreres en hard konflikt. | Automatisk avgjørelse |

### Warnings / diagnostics

| Rule | What it does | Kind |
|---|---|---|
| Skjev kampfordeling: Frisk Asker 1 (Frisk Asker, U7) | Frisk Asker 1 (Frisk Asker, U7) (Frisk Asker, U7) spiller 15 kamper, mens snittet for U7 er 0.0 — 15.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 2 (Frisk Asker, U7) | Frisk Asker 2 (Frisk Asker, U7) (Frisk Asker, U7) spiller 15 kamper, mens snittet for U7 er 0.0 — 15.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 3 (Frisk Asker, U7) | Frisk Asker 3 (Frisk Asker, U7) (Frisk Asker, U7) spiller 15 kamper, mens snittet for U7 er 0.0 — 15.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Holmen Hockey Hvit (Holmen, U7) | Holmen Hockey Hvit (Holmen, U7) (Holmen, U7) spiller 15 kamper, mens snittet for U7 er 0.0 — 15.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Holmen Hockey Rød (Holmen, U7) | Holmen Hockey Rød (Holmen, U7) (Holmen, U7) spiller 15 kamper, mens snittet for U7 er 0.0 — 15.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Holmen Hockey Sort (Holmen, U7) | Holmen Hockey Sort (Holmen, U7) (Holmen, U7) spiller 15 kamper, mens snittet for U7 er 0.0 — 15.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Blå (Jar, U7) | Jar Blå (Jar, U7) (Jar, U7) spiller 15 kamper, mens snittet for U7 er 0.0 — 15.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Grønn (Jar, U7) | Jar Grønn (Jar, U7) (Jar, U7) spiller 15 kamper, mens snittet for U7 er 0.0 — 15.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Hvit (Jar, U7) | Jar Hvit (Jar, U7) (Jar, U7) spiller 15 kamper, mens snittet for U7 er 0.0 — 15.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Rød (Jar, U7) | Jar Rød (Jar, U7) (Jar, U7) spiller 15 kamper, mens snittet for U7 er 0.0 — 15.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Svart (Jar, U7) | Jar Svart (Jar, U7) (Jar, U7) spiller 15 kamper, mens snittet for U7 er 0.0 — 15.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jutul Grønn (Jutul, U7) | Jutul Grønn (Jutul, U7) (Jutul, U7) spiller 15 kamper, mens snittet for U7 er 0.0 — 15.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jutul Hvit (Jutul, U7) | Jutul Hvit (Jutul, U7) (Jutul, U7) spiller 15 kamper, mens snittet for U7 er 0.0 — 15.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jutul Rød (Jutul, U7) | Jutul Rød (Jutul, U7) (Jutul, U7) spiller 15 kamper, mens snittet for U7 er 0.0 — 15.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Sandefjord (Sandefjord Penguins Ishockeyklubb, U7) | Sandefjord (Sandefjord Penguins Ishockeyklubb, U7) (Sandefjord Penguins Ishockeyklubb, U7) spiller 15 kamper, mens snittet for U7 er 0.0 — 15.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Tønsberg (Tønsberg, U7) | Tønsberg (Tønsberg, U7) (Tønsberg, U7) spiller 15 kamper, mens snittet for U7 er 0.0 — 15.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker | Frisk Asker (Frisk Asker, JU8) spiller 15 kamper, mens snittet for JU8 er 9.0 — 6.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar | Jar (Jar, JU8) spiller 15 kamper, mens snittet for JU8 er 9.0 — 6.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jutul Kittens | Jutul Kittens (Jutul, JU8) spiller 15 kamper, mens snittet for JU8 er 9.0 — 6.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Ringerike 1 (Ringerike, JU8) | Ringerike 1 (Ringerike, JU8) (Ringerike, JU8) spiller 15 kamper, mens snittet for JU8 er 0.0 — 15.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Skien (Skien, JU8) | Skien (Skien, JU8) (Skien, JU8) spiller 12 kamper, mens snittet for JU8 er 0.0 — 12.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 1 (Frisk Asker, U8) | Frisk Asker 1 (Frisk Asker, U8) (Frisk Asker, U8) spiller 30 kamper, mens snittet for U8 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 2 (Frisk Asker, U8) | Frisk Asker 2 (Frisk Asker, U8) (Frisk Asker, U8) spiller 30 kamper, mens snittet for U8 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 3 (Frisk Asker, U8) | Frisk Asker 3 (Frisk Asker, U8) (Frisk Asker, U8) spiller 30 kamper, mens snittet for U8 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 4 (Frisk Asker, U8) | Frisk Asker 4 (Frisk Asker, U8) (Frisk Asker, U8) spiller 30 kamper, mens snittet for U8 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 5 (Frisk Asker, U8) | Frisk Asker 5 (Frisk Asker, U8) (Frisk Asker, U8) spiller 30 kamper, mens snittet for U8 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Holmen Hockey Blå (Holmen, U8) | Holmen Hockey Blå (Holmen, U8) (Holmen, U8) spiller 30 kamper, mens snittet for U8 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Holmen Hockey Hvit (Holmen, U8) | Holmen Hockey Hvit (Holmen, U8) (Holmen, U8) spiller 30 kamper, mens snittet for U8 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Holmen Hockey Rød (Holmen, U8) | Holmen Hockey Rød (Holmen, U8) (Holmen, U8) spiller 30 kamper, mens snittet for U8 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Holmen Hockey Sort (Holmen, U8) | Holmen Hockey Sort (Holmen, U8) (Holmen, U8) spiller 30 kamper, mens snittet for U8 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Blå (Jar, U8) | Jar Blå (Jar, U8) (Jar, U8) spiller 30 kamper, mens snittet for U8 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Hvit (Jar, U8) | Jar Hvit (Jar, U8) (Jar, U8) spiller 30 kamper, mens snittet for U8 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Rød (Jar, U8) | Jar Rød (Jar, U8) (Jar, U8) spiller 30 kamper, mens snittet for U8 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Svart (Jar, U8) | Jar Svart (Jar, U8) (Jar, U8) spiller 25 kamper, mens snittet for U8 er 0.0 — 25.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jutul Grønn (Jutul, U8) | Jutul Grønn (Jutul, U8) (Jutul, U8) spiller 30 kamper, mens snittet for U8 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jutul Gul | Jutul Gul (Jutul, U8) spiller 25 kamper, mens snittet for U8 er 1.1 — 23.9 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jutul Hvit (Jutul, U8) | Jutul Hvit (Jutul, U8) (Jutul, U8) spiller 30 kamper, mens snittet for U8 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jutul Rød (Jutul, U8) | Jutul Rød (Jutul, U8) (Jutul, U8) spiller 30 kamper, mens snittet for U8 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Kongsberg (Kongsberg, U8) | Kongsberg (Kongsberg, U8) (Kongsberg, U8) spiller 30 kamper, mens snittet for U8 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Ringerike 1 (Ringerike, U8) | Ringerike 1 (Ringerike, U8) (Ringerike, U8) spiller 30 kamper, mens snittet for U8 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Ringerike 2 (Ringerike, U8) | Ringerike 2 (Ringerike, U8) (Ringerike, U8) spiller 30 kamper, mens snittet for U8 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Sandefjord (Sandefjord Penguins Ishockeyklubb, U8) | Sandefjord (Sandefjord Penguins Ishockeyklubb, U8) (Sandefjord Penguins Ishockeyklubb, U8) spiller 30 kamper, mens snittet for U8 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Skien (Skien, U8) | Skien (Skien, U8) (Skien, U8) spiller 30 kamper, mens snittet for U8 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Tønsberg (Tønsberg, U8) | Tønsberg (Tønsberg, U8) (Tønsberg, U8) spiller 30 kamper, mens snittet for U8 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 1 (Frisk Asker, U9) | Frisk Asker 1 (Frisk Asker, U9) (Frisk Asker, U9) spiller 56 kamper, mens snittet for U9 er 0.0 — 56.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 2 (Frisk Asker, U9) | Frisk Asker 2 (Frisk Asker, U9) (Frisk Asker, U9) spiller 56 kamper, mens snittet for U9 er 0.0 — 56.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 3 (Frisk Asker, U9) | Frisk Asker 3 (Frisk Asker, U9) (Frisk Asker, U9) spiller 56 kamper, mens snittet for U9 er 0.0 — 56.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 4 (Frisk Asker, U9) | Frisk Asker 4 (Frisk Asker, U9) (Frisk Asker, U9) spiller 56 kamper, mens snittet for U9 er 0.0 — 56.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Holmen Hockey Hvit (Holmen, U9) | Holmen Hockey Hvit (Holmen, U9) (Holmen, U9) spiller 56 kamper, mens snittet for U9 er 0.0 — 56.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Holmen Hockey Rød (Holmen, U9) | Holmen Hockey Rød (Holmen, U9) (Holmen, U9) spiller 56 kamper, mens snittet for U9 er 0.0 — 56.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Holmen Hockey Sort (Holmen, U9) | Holmen Hockey Sort (Holmen, U9) (Holmen, U9) spiller 56 kamper, mens snittet for U9 er 0.0 — 56.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Blå (Jar, U9) | Jar Blå (Jar, U9) (Jar, U9) spiller 56 kamper, mens snittet for U9 er 0.0 — 56.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Hvit (Jar, U9) | Jar Hvit (Jar, U9) (Jar, U9) spiller 56 kamper, mens snittet for U9 er 0.0 — 56.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Rød (Jar, U9) | Jar Rød (Jar, U9) (Jar, U9) spiller 56 kamper, mens snittet for U9 er 0.0 — 56.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jutul Grønn (Jutul, U9) | Jutul Grønn (Jutul, U9) (Jutul, U9) spiller 56 kamper, mens snittet for U9 er 0.0 — 56.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jutul Hvit (Jutul, U9) | Jutul Hvit (Jutul, U9) (Jutul, U9) spiller 56 kamper, mens snittet for U9 er 0.0 — 56.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jutul Rød (Jutul, U9) | Jutul Rød (Jutul, U9) (Jutul, U9) spiller 56 kamper, mens snittet for U9 er 0.0 — 56.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Kongsberg (Kongsberg, U9) | Kongsberg (Kongsberg, U9) (Kongsberg, U9) spiller 56 kamper, mens snittet for U9 er 0.0 — 56.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Ringerike 1 (Ringerike, U9) | Ringerike 1 (Ringerike, U9) (Ringerike, U9) spiller 56 kamper, mens snittet for U9 er 0.0 — 56.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Sandefjord (Sandefjord Penguins Ishockeyklubb, U9) | Sandefjord (Sandefjord Penguins Ishockeyklubb, U9) (Sandefjord Penguins Ishockeyklubb, U9) spiller 56 kamper, mens snittet for U9 er 0.0 — 56.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Skien (Skien, U9) | Skien (Skien, U9) (Skien, U9) spiller 56 kamper, mens snittet for U9 er 0.0 — 56.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Tønsberg (Tønsberg, U9) | Tønsberg (Tønsberg, U9) (Tønsberg, U9) spiller 56 kamper, mens snittet for U9 er 0.0 — 56.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker Orange (Frisk Asker, JU10) | Frisk Asker Orange (Frisk Asker, JU10) (Frisk Asker, JU10) spiller 30 kamper, mens snittet for JU10 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker Svart (Frisk Asker, JU10) | Frisk Asker Svart (Frisk Asker, JU10) (Frisk Asker, JU10) spiller 33 kamper, mens snittet for JU10 er 0.0 — 33.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Blå (Jar, JU10) | Jar Blå (Jar, JU10) (Jar, JU10) spiller 30 kamper, mens snittet for JU10 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Hvit (Jar, JU10) | Jar Hvit (Jar, JU10) (Jar, JU10) spiller 28 kamper, mens snittet for JU10 er 0.0 — 28.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Rød (Jar, JU10) | Jar Rød (Jar, JU10) (Jar, JU10) spiller 25 kamper, mens snittet for JU10 er 0.0 — 25.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Ringerike 1 (Ringerike, JU10) | Ringerike 1 (Ringerike, JU10) (Ringerike, JU10) spiller 38 kamper, mens snittet for JU10 er 0.0 — 38.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Skien (Skien, JU10) | Skien (Skien, JU10) (Skien, JU10) spiller 38 kamper, mens snittet for JU10 er 0.0 — 38.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 1 (Frisk Asker, U10) | Frisk Asker 1 (Frisk Asker, U10) (Frisk Asker, U10) spiller 38 kamper, mens snittet for U10 er 0.0 — 38.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 2 (Frisk Asker, U10) | Frisk Asker 2 (Frisk Asker, U10) (Frisk Asker, U10) spiller 40 kamper, mens snittet for U10 er 0.0 — 40.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 3 (Frisk Asker, U10) | Frisk Asker 3 (Frisk Asker, U10) (Frisk Asker, U10) spiller 38 kamper, mens snittet for U10 er 0.0 — 38.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 4 (Frisk Asker, U10) | Frisk Asker 4 (Frisk Asker, U10) (Frisk Asker, U10) spiller 38 kamper, mens snittet for U10 er 0.0 — 38.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 5 (Frisk Asker, U10) | Frisk Asker 5 (Frisk Asker, U10) (Frisk Asker, U10) spiller 40 kamper, mens snittet for U10 er 0.0 — 40.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 6 | Frisk Asker 6 (Frisk Asker, U10) spiller 40 kamper, mens snittet for U10 er 5.1 — 34.9 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Holmen Hockey Blå (Holmen, U10) | Holmen Hockey Blå (Holmen, U10) (Holmen, U10) spiller 40 kamper, mens snittet for U10 er 0.0 — 40.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Holmen Hockey Hvit (Holmen, U10) | Holmen Hockey Hvit (Holmen, U10) (Holmen, U10) spiller 38 kamper, mens snittet for U10 er 0.0 — 38.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Holmen Hockey Rød (Holmen, U10) | Holmen Hockey Rød (Holmen, U10) (Holmen, U10) spiller 40 kamper, mens snittet for U10 er 0.0 — 40.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Holmen Hockey Sort (Holmen, U10) | Holmen Hockey Sort (Holmen, U10) (Holmen, U10) spiller 40 kamper, mens snittet for U10 er 0.0 — 40.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Blå (Jar, U10) | Jar Blå (Jar, U10) (Jar, U10) spiller 40 kamper, mens snittet for U10 er 0.0 — 40.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Grønn (Jar, U10) | Jar Grønn (Jar, U10) (Jar, U10) spiller 40 kamper, mens snittet for U10 er 0.0 — 40.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Gul (Jar, U10) | Jar Gul (Jar, U10) (Jar, U10) spiller 38 kamper, mens snittet for U10 er 0.0 — 38.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Hvit (Jar, U10) | Jar Hvit (Jar, U10) (Jar, U10) spiller 40 kamper, mens snittet for U10 er 0.0 — 40.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Rød (Jar, U10) | Jar Rød (Jar, U10) (Jar, U10) spiller 38 kamper, mens snittet for U10 er 0.0 — 38.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Svart (Jar, U10) | Jar Svart (Jar, U10) (Jar, U10) spiller 38 kamper, mens snittet for U10 er 0.0 — 38.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jutul Grønn (Jutul, U10) | Jutul Grønn (Jutul, U10) (Jutul, U10) spiller 40 kamper, mens snittet for U10 er 0.0 — 40.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jutul Hvit (Jutul, U10) | Jutul Hvit (Jutul, U10) (Jutul, U10) spiller 40 kamper, mens snittet for U10 er 0.0 — 40.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jutul Rød (Jutul, U10) | Jutul Rød (Jutul, U10) (Jutul, U10) spiller 40 kamper, mens snittet for U10 er 0.0 — 40.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Kongsberg (Kongsberg, U10) | Kongsberg (Kongsberg, U10) (Kongsberg, U10) spiller 40 kamper, mens snittet for U10 er 0.0 — 40.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Ringerike 1 (Ringerike, U10) | Ringerike 1 (Ringerike, U10) (Ringerike, U10) spiller 40 kamper, mens snittet for U10 er 0.0 — 40.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Ringerike 2 (Ringerike, U10) | Ringerike 2 (Ringerike, U10) (Ringerike, U10) spiller 38 kamper, mens snittet for U10 er 0.0 — 38.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Ringerike 3 | Ringerike 3 (Ringerike, U10) spiller 40 kamper, mens snittet for U10 er 5.0 — 35.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Ringerike 4 | Ringerike 4 (Ringerike, U10) spiller 40 kamper, mens snittet for U10 er 5.0 — 35.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Tønsberg (Tønsberg, U10) | Tønsberg (Tønsberg, U10) (Tønsberg, U10) spiller 40 kamper, mens snittet for U10 er 0.0 — 40.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 1 (Frisk Asker, U11) | Frisk Asker 1 (Frisk Asker, U11) (Frisk Asker, U11) spiller 48 kamper, mens snittet for U11 er 0.0 — 48.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 2 (Frisk Asker, U11) | Frisk Asker 2 (Frisk Asker, U11) (Frisk Asker, U11) spiller 50 kamper, mens snittet for U11 er 0.0 — 50.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 3 (Frisk Asker, U11) | Frisk Asker 3 (Frisk Asker, U11) (Frisk Asker, U11) spiller 45 kamper, mens snittet for U11 er 0.0 — 45.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 4 (Frisk Asker, U11) | Frisk Asker 4 (Frisk Asker, U11) (Frisk Asker, U11) spiller 50 kamper, mens snittet for U11 er 0.0 — 50.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Holmen Hockey Hvit (Holmen, U11) | Holmen Hockey Hvit (Holmen, U11) (Holmen, U11) spiller 50 kamper, mens snittet for U11 er 0.0 — 50.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Holmen Hockey Rød (Holmen, U11) | Holmen Hockey Rød (Holmen, U11) (Holmen, U11) spiller 50 kamper, mens snittet for U11 er 0.0 — 50.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Blå (Jar, U11) | Jar Blå (Jar, U11) (Jar, U11) spiller 45 kamper, mens snittet for U11 er 0.0 — 45.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Grønn (Jar, U11) | Jar Grønn (Jar, U11) (Jar, U11) spiller 48 kamper, mens snittet for U11 er 0.0 — 48.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Gul (Jar, U11) | Jar Gul (Jar, U11) (Jar, U11) spiller 43 kamper, mens snittet for U11 er 0.0 — 43.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Hvit (Jar, U11) | Jar Hvit (Jar, U11) (Jar, U11) spiller 45 kamper, mens snittet for U11 er 0.0 — 45.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Lilla | Jar Lilla (Jar, U11) spiller 45 kamper, mens snittet for U11 er 4.1 — 40.9 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Oransje | Jar Oransje (Jar, U11) spiller 45 kamper, mens snittet for U11 er 4.1 — 40.9 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Rød (Jar, U11) | Jar Rød (Jar, U11) (Jar, U11) spiller 45 kamper, mens snittet for U11 er 0.0 — 45.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Svart (Jar, U11) | Jar Svart (Jar, U11) (Jar, U11) spiller 43 kamper, mens snittet for U11 er 0.0 — 43.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jutul Grønn (Jutul, U11) | Jutul Grønn (Jutul, U11) (Jutul, U11) spiller 50 kamper, mens snittet for U11 er 0.0 — 50.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jutul Rød (Jutul, U11) | Jutul Rød (Jutul, U11) (Jutul, U11) spiller 50 kamper, mens snittet for U11 er 0.0 — 50.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Kongsberg (Kongsberg, U11) | Kongsberg (Kongsberg, U11) (Kongsberg, U11) spiller 50 kamper, mens snittet for U11 er 0.0 — 50.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Ringerike 1 (Ringerike, U11) | Ringerike 1 (Ringerike, U11) (Ringerike, U11) spiller 50 kamper, mens snittet for U11 er 0.0 — 50.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Ringerike 2 (Ringerike, U11) | Ringerike 2 (Ringerike, U11) (Ringerike, U11) spiller 50 kamper, mens snittet for U11 er 0.0 — 50.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Sandefjord (Sandefjord Penguins Ishockeyklubb, U11) | Sandefjord (Sandefjord Penguins Ishockeyklubb, U11) (Sandefjord Penguins Ishockeyklubb, U11) spiller 50 kamper, mens snittet for U11 er 0.0 — 50.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Skien (Skien, U11) | Skien (Skien, U11) (Skien, U11) spiller 50 kamper, mens snittet for U11 er 0.0 — 50.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Tønsberg Grå (Tønsberg, U11) | Tønsberg Grå (Tønsberg, U11) (Tønsberg, U11) spiller 50 kamper, mens snittet for U11 er 0.0 — 50.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Tønsberg Hvit (Tønsberg, U11) | Tønsberg Hvit (Tønsberg, U11) (Tønsberg, U11) spiller 50 kamper, mens snittet for U11 er 0.0 — 50.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Tønsberg Rød (Tønsberg, U11) | Tønsberg Rød (Tønsberg, U11) (Tønsberg, U11) spiller 50 kamper, mens snittet for U11 er 0.0 — 50.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker Orange (Frisk Asker, JU12) | Frisk Asker Orange (Frisk Asker, JU12) (Frisk Asker, JU12) spiller 27 kamper, mens snittet for JU12 er 0.0 — 27.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker Svart (Frisk Asker, JU12) | Frisk Asker Svart (Frisk Asker, JU12) (Frisk Asker, JU12) spiller 24 kamper, mens snittet for JU12 er 0.0 — 24.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jutul/Jar Kittens | Jutul/Jar Kittens (Jutul, JU12) spiller 27 kamper, mens snittet for JU12 er 8.1 — 18.9 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Kongsberg/Tønsberg | Kongsberg/Tønsberg (Kongsberg/Tønsberg, JU12) spiller 30 kamper, mens snittet for JU12 er 8.1 — 21.9 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Ringerike 1 (Ringerike, JU12) | Ringerike 1 (Ringerike, JU12) (Ringerike, JU12) spiller 27 kamper, mens snittet for JU12 er 0.0 — 27.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Ringerike 2 (Ringerike, JU12) | Ringerike 2 (Ringerike, JU12) (Ringerike, JU12) spiller 27 kamper, mens snittet for JU12 er 0.0 — 27.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Skien (Skien, JU12) | Skien (Skien, JU12) (Skien, JU12) spiller 30 kamper, mens snittet for JU12 er 0.0 — 30.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 1 (Frisk Asker, U12) | Frisk Asker 1 (Frisk Asker, U12) (Frisk Asker, U12) spiller 36 kamper, mens snittet for U12 er 0.0 — 36.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 2 (Frisk Asker, U12) | Frisk Asker 2 (Frisk Asker, U12) (Frisk Asker, U12) spiller 36 kamper, mens snittet for U12 er 0.0 — 36.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Frisk Asker 3 (Frisk Asker, U12) | Frisk Asker 3 (Frisk Asker, U12) (Frisk Asker, U12) spiller 33 kamper, mens snittet for U12 er 0.0 — 33.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Holmen Hockey Hvit (Holmen, U12) | Holmen Hockey Hvit (Holmen, U12) (Holmen, U12) spiller 33 kamper, mens snittet for U12 er 0.0 — 33.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Holmen Hockey Rød (Holmen, U12) | Holmen Hockey Rød (Holmen, U12) (Holmen, U12) spiller 33 kamper, mens snittet for U12 er 0.0 — 33.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Blå (Jar, U12) | Jar Blå (Jar, U12) (Jar, U12) spiller 36 kamper, mens snittet for U12 er 0.0 — 36.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Hvit (Jar, U12) | Jar Hvit (Jar, U12) (Jar, U12) spiller 33 kamper, mens snittet for U12 er 0.0 — 33.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Rød (Jar, U12) | Jar Rød (Jar, U12) (Jar, U12) spiller 33 kamper, mens snittet for U12 er 0.0 — 33.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jar Svart (Jar, U12) | Jar Svart (Jar, U12) (Jar, U12) spiller 33 kamper, mens snittet for U12 er 0.0 — 33.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jutul Grønn (Jutul, U12) | Jutul Grønn (Jutul, U12) (Jutul, U12) spiller 36 kamper, mens snittet for U12 er 0.0 — 36.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Jutul Rød (Jutul, U12) | Jutul Rød (Jutul, U12) (Jutul, U12) spiller 36 kamper, mens snittet for U12 er 0.0 — 36.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Kongsberg (Kongsberg, U12) | Kongsberg (Kongsberg, U12) (Kongsberg, U12) spiller 36 kamper, mens snittet for U12 er 0.0 — 36.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Ringerike 1 (Ringerike, U12) | Ringerike 1 (Ringerike, U12) (Ringerike, U12) spiller 36 kamper, mens snittet for U12 er 0.0 — 36.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Ringerike 2 (Ringerike, U12) | Ringerike 2 (Ringerike, U12) (Ringerike, U12) spiller 36 kamper, mens snittet for U12 er 0.0 — 36.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Tønsberg Grå (Tønsberg, U12) | Tønsberg Grå (Tønsberg, U12) (Tønsberg, U12) spiller 36 kamper, mens snittet for U12 er 0.0 — 36.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Tønsberg Hvit (Tønsberg, U12) | Tønsberg Hvit (Tønsberg, U12) (Tønsberg, U12) spiller 33 kamper, mens snittet for U12 er 0.0 — 33.0 flere enn snittet. | Anbefaling |
| Skjev kampfordeling: Tønsberg Rød (Tønsberg, U12) | Tønsberg Rød (Tønsberg, U12) (Tønsberg, U12) spiller 33 kamper, mens snittet for U12 er 0.0 — 33.0 flere enn snittet. | Anbefaling |
| Klubbbelastning per turnering | Kjører en advarsel når en klubb har flere lag i en turnering enn det effektive taket tillater; 0 tilfelle(r) er registrert i denne planen. | Advarsel |
| Hjemmeturneringsfordeling | Kjører en advarsel når en klubb avviker for mye fra proporsjonal hjemmeturneringsfordeling; 0 tilfelle(r) er registrert i denne planen. | Advarsel |
| Kampbalanse og sesongdekning | Kjører en advarsel når kampantall spres for mye mellom lag eller når et lag har et opphold i sesongdekningen (sesongstart -> første turnering -> ... -> siste turnering -> sesongslutt) som overstiger terskelen; 0 tilfelle(r) er registrert i denne planen. | Advarsel |
| Skjev kampfordeling per aldersgruppe | Kjører en advarsel når et lag avviker for mye fra aldersgruppens forventede kampmengde; 142 tilfelle(r) er registrert i denne planen. | Advarsel |
| Feasibility / kapasitet | Kjører en advarsel når sesongvinduet sannsynligvis ikke kan oppfylle deltakelsesmålet; 0 tilfelle(r) er registrert i denne planen. | Advarsel |
| Fallback vertsklubb | Kjører en advarsel når planleggeren må beholde opprinnelig vertsklubb fordi ønsket slot ikke finnes; 0 tilfelle(r) er registrert i denne planen. | Advarsel |
| Månedslast | Kjører en advarsel når en måned avviker mer enn terskelen fra forventet turneringslast; 0 tilfelle(r) er registrert i denne planen. | Advarsel |

## Final verification and publication readiness

The search-time planning contract keeps `ok` narrowly defined as structural validity so planners and optimizers can compare candidates without turning every unresolved operational task into a hard solver failure. Immediately before production export, the stricter final verifier additionally checks that production tournaments are not below the three-team minimum and that each tournament's game list is a complete, non-duplicated round robin with no team scheduled twice in one round.

The final verifier publishes a separate readiness state:

- `INVALID`: at least one hard structural/integrity violation exists.
- `REVIEW_REQUIRED`: structurally valid, but unresolved hosting, calendar placement, external calendar conflict, participation shortfall, or incomplete verification remains.
- `PUBLISHABLE`: hard verification passed and none of those unresolved obligations remains.

This classification is evidence for the operator/publication decision; it does not weaken the existing explicit human confirmation required to publish.

## Primary source files

- `tournament_scheduler/rules_report.py`
- `tournament_scheduler/rules_report_capacity_rules.py`
- `tournament_scheduler/rules_report_operational_rules.py`
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
