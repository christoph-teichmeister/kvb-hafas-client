# KVB HAFAS API — Referenz

Diese Doku beschreibt den reverse-engineerten HAFAS-Endpoint, den die KVB
intern für ihre eigene Fahrplanauskunft und den Widget-Generator nutzt.
Alles hier basiert auf eigenen Tests (siehe Datumsangaben bei den
Beispielen), nicht auf offizieller KVB-Dokumentation — es gibt keine.

## Inhaltsverzeichnis

- [Grundlagen](#grundlagen)
- [Auth](#auth)
- [Methoden](#methoden)
  - [LocMatch — Haltestellen suchen](#locmatch--haltestellen-suchen)
  - [LocGeoPos — Haltestellen in der Nähe](#locgeopos--haltestellen-in-der-nähe)
  - [StationBoard — Abfahrtstafel](#stationboard--abfahrtstafel)
  - [JourneyDetails — Einzelfahrt im Detail](#journeydetails--einzelfahrt-im-detail)
  - [TripSearch — Verbindungssuche](#tripsearch--verbindungssuche)
  - [HimSearch — Störungsmeldungen](#himsearch--störungsmeldungen)
- [Historische Daten](#historische-daten)
- [Auslastungsdaten](#auslastungsdaten)
- [Bekannte Fehlercodes](#bekannte-fehlercodes)
- [Wie das gefunden wurde](#wie-das-gefunden-wurde)

## Grundlagen

- **Endpoint:** `POST https://auskunft.kvb.koeln/gate`
- **Content-Type:** `application/json`
- **Protokoll:** HAFAS `mgate` (JSON-Variante), Hersteller HaCon — dasselbe
  System, das u.a. die Deutsche Bahn, viele Verkehrsverbünde und
  internationale Betreiber nutzen.
- Jede Anfrage ist ein `POST` mit einem Envelope, der eine oder mehrere
  `svcReqL`-Einträge enthält. Jeder Eintrag hat eine `meth` (Methode) und
  ein `req` (methodenspezifische Parameter).

### Basis-Envelope

```json
{
  "id": "1@",
  "ver": "1.16",
  "lang": "deu",
  "auth": { "type": "AID", "aid": "Rt6foY5zcTTRXMQs" },
  "client": { "id": "HAFAS", "type": "WEB" },
  "svcReqL": [
    { "meth": "<METHODE>", "req": { /* ... */ } }
  ]
}
```

Die Response spiegelt das: `svcResL` enthält ein Ergebnis pro angefragter
Methode, jeweils mit `err` (Statuscode, `"OK"` bei Erfolg) und `res`
(Nutzdaten). Mehrere `svcReqL`-Einträge in einem Request werden batch-weise
in einer Response beantwortet — spart Roundtrips, wenn du z.B. mehrere
Haltestellen auf einmal abfragen willst.

## Auth

```json
"auth": { "type": "AID", "aid": "Rt6foY5zcTTRXMQs" }
```

Kein Checksum/Salt-Mechanismus nötig (manche HAFAS-Installationen verlangen
das zusätzlich als `mac`-Feld — KVB offenbar nicht). Die `aid` ist Teil des
öffentlich ausgelieferten Frontend-JS, keine geheime Anmeldeinformation.

## Methoden

### LocMatch — Haltestellen suchen

Volltextsuche nach Haltestellennamen. Das `?` am Ende von `name` scheint
Prefix-/Fuzzy-Matching zu aktivieren.

```json
{
  "meth": "LocMatch",
  "req": {
    "input": {
      "field": "S",
      "loc": { "name": "Neumarkt?", "type": "S" },
      "maxLoc": 5
    }
  }
}
```

**Response** (`res.match.locL[]`): Liste von Orten mit `name`, `extId`
(die Stop-ID, die alle anderen Methoden erwarten), `crd` (Koordinaten als
`{x, y}`, jeweils `* 1_000_000`, `x` = Longitude, `y` = Latitude).

### LocGeoPos — Haltestellen in der Nähe

Umkreissuche nach Koordinaten.

```json
{
  "meth": "LocGeoPos",
  "req": {
    "ring": { "cCrd": { "x": 6959800, "y": 50936600 }, "maxDist": 500 },
    "maxLoc": 10
  }
}
```

⚠️ Liefert ohne zusätzlichen Filter **auch POIs** (Museen, Sehenswürdigkeiten
etc.), nicht nur Haltestellen — im Test kamen z.B. "Gürzenich" und
"Wallraf-Richartz-Museum" zurück. HAFAS unterstützt normalerweise einen
`locFltrL`-Parameter mit einer Produkt-Typ-Bitmaske, um auf ÖPNV-Haltestellen
einzuschränken; die korrekte Maske für KVB haben wir noch nicht verifiziert
(ein Testwert `"1023"` führte zu keiner sichtbaren Filterung).

### StationBoard — Abfahrtstafel

Die Kernmethode für Echtzeit-Abfahrten.

```json
{
  "meth": "StationBoard",
  "req": {
    "type": "DEP",
    "stbLoc": { "extId": "900000002" },
    "maxJny": 10
  }
}
```

- `type`: `"DEP"` (Abfahrten) oder `"ARR"` (Ankünfte).
- `date`/`time` optional (Format `YYYYMMDD` / `HHMMSS`) — ohne Angabe wird
  "jetzt" angenommen. Auch für Zukunft/Vergangenheit innerhalb der aktuellen
  Fahrplanperiode nutzbar, siehe [Historische Daten](#historische-daten).

**Response** (`res.jnyL[]`), pro Journey u.a.:

| Feld | Bedeutung |
|---|---|
| `stbStop.dTimeS` | geplante Abfahrtszeit (Soll) |
| `stbStop.dTimeR` | Echtzeit-Prognose (Ist) — fehlt bei rein geplanten/vergangenen Fahrten |
| `stbStop.dPlatfS` / `dPlatfR` | Gleis/Bahnsteig, Soll/Ist |
| `prodX` | Index in `res.common.prodL[]` → dort `name` = Linienbezeichnung (z.B. "146") |
| `dirTxt` | Zielhaltestelle/Richtungstext |
| `isCncl` | `true` bei Ausfall (im Test durchweg `false` — bei echten Ausfällen noch nicht verifiziert) |
| `jid` | Journey-ID, Eingabe für `JourneyDetails` |

### JourneyDetails — Einzelfahrt im Detail

Alle Zwischenhalte einer einzelnen Fahrt, inkl. Soll/Ist-Zeiten pro Halt.

```json
{
  "meth": "JourneyDetails",
  "req": { "jid": "1|2100|1|1|15092026" }
}
```

`jid` kommt aus einem `StationBoard`- oder `TripSearch`-Ergebnis (Journeys
sind zeitlich begrenzt gültig — alte `jid`s aus vorherigen Tagen
funktionieren vermutlich nicht mehr).

**Response** (`res.journey.stopL[]`): jeder Zwischenhalt mit `aTimeS`/`aTimeR`
(Ankunft Soll/Ist), `dTimeS`/`dTimeR` (Abfahrt Soll/Ist), `idx` (Position in
der Fahrt).

### TripSearch — Verbindungssuche

Klassische "Von A nach B"-Routenplanung mit Umstiegen.

```json
{
  "meth": "TripSearch",
  "req": {
    "depLocL": [{ "extId": "900000002" }],
    "arrLocL": [{ "extId": "900000001" }],
    "outDate": "20260916",
    "outTime": "120000"
  }
}
```

**Response** (`res.outConL[]`): Verbindungen mit `dep`/`arr` (Zeiten) und
`secL[]` (einzelne Teilstrecken/Umstiege). Im Test kamen z.B. 3 Verbindungen
für eine simple Direktstrecke zurück (unterschiedliche Abfahrtszeiten).

### HimSearch — Störungsmeldungen

Existiert (Störungsmeldungen tauchen z.B. als `himL`/`himMsgEventL` im
`common`-Block von `StationBoard`-Responses auf), aber die eigenständige
Abfrage ist noch nicht sauber reverse-engineered:

```json
{
  "meth": "HimSearch",
  "req": { "himFltrL": [{ "type": "STATION", "mode": "INC", "value": "900000002" }] }
}
```

Ein Testaufruf mit diesem Filter ergab einen `PARSE`-Fehler auf Top-Level.
Gültige `type`-Werte laut Fehlermeldung eines anderen Tests: `EID, SRC,
DEPT, HIMID, TRAIN, HIMCAT, PID, HIMTAG, COMP, TXT, OPR, LINE, SENDER,
GLINEID, PROD, AFLD, HIMTXT, LINEID, STATION, CAT, ADMIN, META, CH, UIC,
REG` — Groß-/Kleinschreibung ist relevant (`"line"` schlägt fehl, `"LINE"`
vermutlich nicht, aber ungetestet). **Offener TODO**, siehe unten.

## Historische Daten

**Kurzfassung: Nein, nicht wirklich.**

- `StationBoard` akzeptiert ein `date`-Feld auch für die Vergangenheit —
  aber nur innerhalb der **aktuellen Fahrplanperiode**. Getestet:
  - `2026-01-01`, `2026-02-01`, `2025-12-15` → funktioniert (`err: OK`)
  - `2025-12-13` und früher → `err: H9360` ("Date outside of the timetable
    period") — die aktuelle Periode beginnt demnach ca. **14.-15.
    Dezember 2025** (üblicher bundesweiter Fahrplanwechsel-Termin).
  - `2025-09-15` (ein Jahr zurück) → ebenfalls `H9360`.
- Und selbst innerhalb der gültigen Periode: für Tage in der Vergangenheit
  liefert die Antwort nur die **geplanten** Zeiten (`dTimeS`), das
  `dTimeR`-Feld (Echtzeit-Ist-Wert) fehlt. Es ist also nur der **Fahrplan**
  einsehbar, nicht was tatsächlich passiert ist (keine
  Ist-Verspätungen, keine tatsächlichen Ausfälle im Nachhinein).

Für echte Verlaufsdaten (Ist-Werte, Ausfallquoten über Zeit) gäbe es zwei
Wege, beide außerhalb dieser API:
1. **Selbst sammeln**: `StationBoard` regelmäßig pollen und die
   Ist-Werte + `isCncl`-Flags in einer eigenen DB persistieren — das ist der
   einzige Weg, an echte historische Ist-Daten zu kommen, wenn man nicht bei
   der KVB direkt anfragt.
2. **KVB direkt fragen**, ob sie interne Betriebsstatistiken (SAE/ITCS-Daten)
   rausgeben — unwahrscheinlich, aber nicht auszuschließen.

## Auslastungsdaten

**Nicht gefunden.** Weder in `StationBoard`- noch in `JourneyDetails`-
Antworten gibt es ein Auslastungs-/Kapazitätsfeld (in anderen HAFAS-
Installationen z.B. `"occ"` bei Fernverkehr). Vermutung: KVB-Fahrzeuge
liefern diese Telemetrie entweder gar nicht oder sie fließt nicht in dieses
Frontend-Backend. Nicht abschließend verifiziert — falls es doch einen Weg
gibt, vermutlich über eine andere `req`-Option bei `StationBoard`
(z.B. `showPassList` oder ähnliche undokumentierte Flags), die wir noch
nicht durchprobiert haben.

## Bekannte Fehlercodes

| Code | Bedeutung |
|---|---|
| `OK` | Erfolg |
| `H9360` | Datum außerhalb der gültigen Fahrplanperiode |
| `PARSE` | Fehlerhafter Request-Body (Top-Level `err`, nicht in `svcResL`) |
| `HAMM` | Fehler beim Deserialisieren eines Feldwerts (z.B. falscher Enum-Wert), ebenfalls Top-Level |
| `FAIL` | Generischer Fehler (in Tests nur gemockt, nicht live gesehen) |

## Wie das gefunden wurde

1. `https://www.kvb.koeln/fahrtinfo/widget-generator/index.html` verlinkt auf
   `https://auskunft.kvb.koeln/widgetgenerator.html`.
2. Diese Seite lädt `js/hafas_webapp_config.js`, was via
   `_.externalConfigPath = "config/webapp.config.json"` weiterverweist.
3. `https://auskunft.kvb.koeln/config/webapp.config.json` enthält
   `"urlMgate": "https://auskunft.kvb.koeln/gate"` und die `aid`.
4. Ab da: Standard-HAFAS-`mgate`-Protokoll, dokumentiert in diversen
   Community-Projekten (z.B. [hafas-client](https://github.com/public-transport/hafas-client)
   auf npm, oder [derf's EFA/HAFAS-Notizen](https://finalrewind.org/interblag/entry/efa-json-api/)).

Keine Zugangsdaten umgangen — alles hier ist aus öffentlich ausgeliefertem
Frontend-Code ableitbar. Siehe README.md für den rechtlichen Disclaimer.
