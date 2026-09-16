# KVB HAFAS API — Referenz

Reverse-engineerter HAFAS-Endpoint der KVB (Fahrplanauskunft, Widget-Generator).
Es gibt keine offizielle Doku — alles hier ist selbst getestet, Stand 2026-09-16.

## Inhaltsverzeichnis

- [Grundlagen](#grundlagen)
- [Haltestellen-IDs](#haltestellen-ids)
- [Methoden](#methoden)
    - [LocMatch — Haltestellen suchen](#locmatch--haltestellen-suchen)
    - [LocGeoPos — Haltestellen in der Nähe](#locgeopos--haltestellen-in-der-nähe)
    - [LocDetails — Haltestelle im Detail](#locdetails--haltestelle-im-detail)
    - [LocGeoReach — Isochrone](#locgeoreach--isochrone)
    - [StationBoard — Abfahrtstafel](#stationboard--abfahrtstafel)
    - [JourneyDetails — Einzelfahrt im Detail](#journeydetails--einzelfahrt-im-detail)
    - [JourneyGeoPos — Live-Fahrzeugpositionen](#journeygeopos--live-fahrzeugpositionen)
    - [JourneyMatch — Fahrten nach Linie](#journeymatch--fahrten-nach-linie)
    - [JourneyCourse — Linienverlauf als Polyline](#journeycourse--linienverlauf-als-polyline)
    - [TripSearch — Verbindungssuche](#tripsearch--verbindungssuche)
    - [Reconstruction — Verbindung wiederherstellen](#reconstruction--verbindung-wiederherstellen)
    - [SearchOnTrip — Alternativen zu einer Verbindung](#searchontrip--alternativen-zu-einer-verbindung)
    - [GisRoute — Fußweg straßengenau](#gisroute--fußweg-straßengenau)
    - [HimSearch — Störungsmeldungen](#himsearch--störungsmeldungen)
    - [HimGeoPos — Störungen im Kartenausschnitt](#himgeopos--störungen-im-kartenausschnitt)
    - [HimMatch — aktuell betroffene Haltestellen](#himmatch--aktuell-betroffene-haltestellen)
    - [LineMatch / LineDetails — Linien](#linematch--linedetails--linien)
- [LineSearch — kompletter Linienkatalog](#linesearch--kompletter-linienkatalog)
    - [LineGeoPos — Linien im Umkreis](#linegeopos--linien-im-umkreis)
    - [ServerInfo — Fahrplanperiode](#serverinfo--fahrplanperiode)
- [Methoden-Inventar](#methoden-inventar)
- [Historische Daten](#historische-daten)
- [Auslastungsdaten](#auslastungsdaten)
- [Bekannte Fehlercodes](#bekannte-fehlercodes)
- [Herkunft](#herkunft)

## Grundlagen

- **Endpoint:** `POST https://auskunft.kvb.koeln/gate`, `application/json`
- **Protokoll:** HAFAS `mgate` (JSON-Variante), Hersteller HaCon
- Request = Envelope mit `svcReqL[]`, je Eintrag `meth` + `req`.
  Response = `svcResL[]` mit `err` (`"OK"` bei Erfolg) und `res`.
  Mehrere `svcReqL`-Einträge werden in einer Response batch-weise beantwortet — **und das funktioniert hier auch
  wirklich**: ein POST mit 10
  `JourneyDetails`-Einträgen kommt in ~400 ms zurück, jeder Eintrag mit eigenem
  `common`-Block, Reihenfolge = Reihenfolge der Anfragen. Gemischte Methoden in
  einem POST gehen ebenfalls. Das spart nicht nur Roundtrips, sondern vor allem
  die selbstauferlegte Rate-Limit-Pause pro Fahrt. Client: `_call_many()`,
  benutzt von `journey_routes()`.

  ⚠️ Fehlerhafte Teil-Anfragen kommen als Eintrag mit `err != "OK"` zurück, der
  Rest der Batch bleibt gültig. Wer Antworten positionell zuordnet, muss die
  Länge von `svcResL` gegen die der Anfrage prüfen — sonst rutscht bei einer
  fehlenden Antwort alles um eine Position.
- **Auth:** `{"type": "AID", "aid": "Rt6foY5zcTTRXMQs"}` — kein Checksum/Salt (`mac`-Feld) nötig. Die `aid` steht im
  öffentlich ausgelieferten Frontend-JS.
- **Koordinaten:** `{x, y}`, jeweils `* 1_000_000`; `x` = Longitude, `y` = Latitude.
- **Linienfarben:** `prodL[].icoX` → `common.icoL[].bg`/`.fg` — HAFAS liefert die
  offiziellen Farben mit, für die Stadtbahn exakt die aus dem KVB-Netzplan (Linie 1 `#e0071c`, 15 `#5aac31`, 18
  `#1d92d1`).

```json
{
  "id": "1@",
  "ver": "1.16",
  "lang": "deu",
  "auth": {
    "type": "AID",
    "aid": "Rt6foY5zcTTRXMQs"
  },
  "client": {
    "id": "HAFAS",
    "type": "WEB"
  },
  "svcReqL": [
    {
      "meth": "<METHODE>",
      "req": {}
    }
  ]
}
```

## Haltestellen-IDs

| Form        | Beispiel    | Bedeutung                                                                                                                             |
|-------------|-------------|---------------------------------------------------------------------------------------------------------------------------------------|
| `900xxxxxx` | `900000178` | **Master-Haltestelle** — was `find_stops()` liefert und jede Methode als `extId` erwartet                                             |
| `300xxxxxx` | `300000201` | **Steig/Mast** — einzelner Bahnsteig. Taucht in `common.locL`, `LocGeoReach` und HIM-Loc-Referenzen auf; `mMastLocX` führt zum Master |
| `1`–`949`   | `178`       | **KVB-interne Haltestellennummer**, wie in den URLs der Publikums-Website                                                             |

```
extId = 900000000 + kvb_nummer
```

Verifiziert an 8/8 Stichproben. Die KVB-Website listet unter
`/haltestellen/overview/` genau die 949 KVB-Haltestellen — HAFAS kann das nicht (`LocMatch` braucht Suchbegriff,
`LocGeoPos` Koordinaten, beide liefern auch
VRS/VRR/NL). Wer die Verbundgrenze sauber ziehen will, holt sich diese Liste
extern und rechnet sie per Formel um; dieser Client tut das nicht (kein Scraping).

## Methoden

### LocMatch — Haltestellen suchen

Volltextsuche nach Haltestellennamen.

```json
{
  "meth": "LocMatch",
  "req": {
    "input": {
      "field": "S",
      "loc": {
        "name": "Neumarkt?",
        "type": "S"
      },
      "maxLoc": 5
    }
  }
}
```

**Response** (`res.match.locL[]`): `name`, `extId`, `crd`.

- `?` am Ende von `name` aktiviert Prefix-/Fuzzy-Matching.

### LocGeoPos — Haltestellen in der Nähe

```json
{
  "meth": "LocGeoPos",
  "req": {
    "ring": {
      "cCrd": {
        "x": 6959800,
        "y": 50936600
      },
      "maxDist": 500
    },
    "maxLoc": 10
  }
}
```

- ⚠️ Liefert **auch POIs** (Museen, Sehenswürdigkeiten), nicht nur Haltestellen.
  Der HAFAS-übliche `locFltrL` mit Produkt-Bitmaske ist für KVB nicht verifiziert (`"1023"` bewirkte nichts).

### LocDetails — Haltestelle im Detail

```json
{
  "meth": "LocDetails",
  "req": {
    "locL": [
      {
        "type": "S",
        "lid": "A=1@L=900000002@"
      }
    ]
  }
}
```

**Response** (`res.locL[0]`):

| Feld         | Bedeutung                                                            |
|--------------|----------------------------------------------------------------------|
| `pRefL`      | Indizes in `common.prodL[]` → **alle Linien, die den Halt bedienen** |
| `stopLocL`   | Indizes aller Steige/Masten                                          |
| `entryLocL`  | Zugänge/Eingänge                                                     |
| `isMainMast` | `true` beim Master-Eintrag (`900xxxxxx`)                             |
| `wt`         | interner "weight" (Bedeutung der Haltestelle im Netz)                |

- `lid`-Form `A=1@L=<extId>@` ist Pflicht, blankes `{"extId": …}` reicht nicht.
- `common.prodL` enthält **mehr** Linien als `pRefL` referenziert (Neumarkt:
  14 vs. 9) — nur die referenzierten gelten.
- `pRefL` ist unvollständig gegenüber [`LineGeoPos`](#linegeopos--linien-im-umkreis)
  (Nachtlinien fehlen).

### LocGeoReach — Isochrone

Alle Haltestellen, die von einem Startpunkt in X Minuten erreichbar sind.

```json
{
  "meth": "LocGeoReach",
  "req": {
    "loc": {
      "type": "S",
      "lid": "A=1@L=900000002@"
    },
    "maxDur": 15,
    "maxChg": 0,
    "date": "20260916",
    "time": "120000"
  }
}
```

**Response** (`res.posL[]`): `locX`, `dur` (Minuten), `chg`, `prodX`, `lastLocX`.

- `date`/`time` optional.
- ⚠️ `posL` zeigt auf **Steige** (`300xxxxxx`), nicht auf Haltestellen — über
  `mMastLocX` auf den Master auflösen und je Haltestelle den schnellsten Eintrag
  behalten (Neumarkt/15 min/0 Umstiege: 132 Einträge, deutlich weniger Haltestellen).
- `getPoly` → `HAMM`. Keine Isochronen-Fläche, nur die Liste.

### StationBoard — Abfahrtstafel

```json
{
  "meth": "StationBoard",
  "req": {
    "type": "DEP",
    "stbLoc": {
      "extId": "900000002"
    },
    "maxJny": 10
  }
}
```

**Response** (`res.jnyL[]`):

| Feld                          | Bedeutung                                                         |
|-------------------------------|-------------------------------------------------------------------|
| `stbStop.dTimeS`              | geplante Abfahrtszeit (Soll)                                      |
| `stbStop.dTimeR`              | Echtzeit-Prognose (Ist) — fehlt bei geplanten/vergangenen Fahrten |
| `stbStop.dPlatfS` / `dPlatfR` | Gleis/Bahnsteig, Soll/Ist                                         |
| `prodX`                       | Index in `res.common.prodL[]` → `name` = Linienbezeichnung        |
| `dirTxt`                      | Zielhaltestelle/Richtungstext                                     |
| `isCncl`                      | `true` bei Ausfall (noch nicht gegen echten Ausfall verifiziert)  |
| `jid`                         | Journey-ID, Eingabe für `JourneyDetails`                          |

- `type`: `"DEP"` oder `"ARR"`.
- `date`/`time` optional (`YYYYMMDD` / `HHMMSS`), Default „jetzt". Nur innerhalb
  der aktuellen Fahrplanperiode, siehe [Historische Daten](#historische-daten).

### JourneyDetails — Einzelfahrt im Detail

```json
{
  "meth": "JourneyDetails",
  "req": {
    "jid": "1|2100|1|1|15092026"
  }
}
```

**Response** (`res.journey.stopL[]`): je Halt `aTimeS`/`aTimeR`, `dTimeS`/`dTimeR`,
`idx`.

- `jid` aus `StationBoard` oder `TripSearch`; nur zeitlich begrenzt gültig.
- `getPolyline: true` + `getPasslist: true` liefern zusätzlich die Geometrie —
  deutlich feiner als [`JourneyCourse`](#journeycourse--linienverlauf-als-polyline):
  Linie 18 hat 117 Punkte auf 30 Halte (~4 Stützpunkte je Haltestellenpaar),
  mit `ppLocRefL` als Zuordnung Halt → Punkt.

### JourneyGeoPos — Live-Fahrzeugpositionen

Alle Fahrzeuge in einer Bounding-Box.

```json
{
  "meth": "JourneyGeoPos",
  "req": {
    "maxJny": 100,
    "onlyRT": false,
    "rect": {
      "llCrd": {
        "x": 6900000,
        "y": 50880000
      },
      "urCrd": {
        "x": 7020000,
        "y": 50990000
      }
    },
    "perSize": 120000,
    "perStep": 30000,
    "ageOfReport": true,
    "trainPosMode": "CALC"
  }
}
```

**Response** (`res.jnyL[]`), zusätzlich zu den üblichen Journey-Feldern:

| Feld    | Bedeutung                                                                                                    |
|---------|--------------------------------------------------------------------------------------------------------------|
| `pos`   | aktuelle Position `{x, y}`                                                                                   |
| `ani`   | Animations-Track: `mSec[]` (ms-Offsets), `proc[]`, `dirGeo[]`, `fLocX[]`/`tLocX[]`, `polyG` → `common.polyL` |
| `stopL` | Halte der Fahrt mit Soll/Ist-Zeiten                                                                          |

- ⚠️ `ani.proc[]` ist **Prozent** (0–100), nicht Promille — gegen die
  mitgelieferte Polyline gemessen (9 m mittlere Abweichung gegen 541 m).
- `trainPosMode: "CALC"`: Position wird aus Fahrplan + Prognose hochgerechnet, **kein GPS**.
- `perSize`/`perStep` steuern Länge und Auflösung des Tracks.
- Die Box liefert alles im Bediengebiet, auch Regionalverkehr anderer Betreiber.

### JourneyMatch — Fahrten nach Linie

```json
{
  "meth": "JourneyMatch",
  "req": {
    "input": "18",
    "date": "20260916",
    "time": "120000"
  }
}
```

**Response** (`res.jnyL[]`): `jid`, `stopL` (nur erster und letzter Halt), `pos`,
`sDaysL[]`:

| Feld     | Beispiel                                                        |
|----------|-----------------------------------------------------------------|
| `sDaysI` | `"1. Jul bis 30. Sep 2026 Mo - Fr; nicht 10. bis 28. Aug 2026"` |
| `sDaysR` | `"nicht täglich"`                                               |
| `sDaysB` | dieselbe Info als Bitmaske (Hex, ein Bit pro Betriebstag)       |

- `date` **und** `time` sind Pflicht — ohne sie `FAIL`.
- Einzige Methode, die **Verkehrstage** liefert.

### JourneyCourse — Linienverlauf als Polyline

```json
{
  "meth": "JourneyCourse",
  "req": {
    "jid": "1|4809|1|1|16092026"
  }
}
```

**Response** `res.common.polyL[0]`:

- `crdEncYX`: **Google-Encoded-Polyline** (`delta: true`, `dim: 2`,
  `type: "WGS84"`, Faktor `1e5`).
- `ppLocRefL`: `{locX, ppIdx}` — verknüpft Polyline-Punkte mit Halten.

- Ein Punkt **pro Halt**, keine Straßengeometrie. Für feinere Verläufe
  `JourneyDetails` mit `getPolyline`.

### TripSearch — Verbindungssuche

```json
{
  "meth": "TripSearch",
  "req": {
    "depLocL": [
      {
        "extId": "900000002"
      }
    ],
    "arrLocL": [
      {
        "extId": "900000001"
      }
    ],
    "outDate": "20260916",
    "outTime": "120000"
  }
}
```

**Response** (`res.outConL[]`): Verbindungen mit `dep`/`arr` und `secL[]`
(Teilstrecken/Umstiege). Jede Verbindung bringt ein `ctxRecon`-Token mit,
`WALK`-Abschnitte ein `gis.ctx`.

**Preis:** ungefragt mit dabei, kein Request-Feld nötig — `trfRes` an der
Verbindung, `ovwTrfRefL` zeigt auf den gültigen Eintrag:

```json
"ovwTrfRefL": [
  {
    "fareSetX": 0,
    "fareX": 0,
    "type": "F"
  }
],
"trfRes": {
  "fareSetL": [
    {
      "desc": "Rheinlandtarif",
      "fareL": [
        {
          "cur": "EUR",
          "name": "Preisstufe K",
          "prc": 290
        }
      ]
    }
  ],
  "statusCode": "OK"
}
```

`prc` in Cent. Nicht jede Verbindung hat ein `trfRes` (z.B. reine Fußwege).
Das ist die einzige funktionierende Preisquelle der API — `TariffSearch` bleibt
unerreichbar, wird aber auch nicht gebraucht.

**Praktisch nutzbare Optionen** (alle live geprüft):

| Feld       | Wirkung                                                                                                                                                            |
|------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `numF`     | Gewünschte Anzahl Verbindungen. Der Server liefert auch mal eine mehr (`numF: 6` → 7).                                                                             |
| `ctxScr`   | Blättern: `outCtxScrF`/`outCtxScrB` der Antwort zurückschicken → spätere bzw. frühere Verbindungen. `depLocL`/`arrLocL` müssen mit, der Token allein reicht nicht. |
| `viaLocL`  | `[{"loc": {"extId": "900000001"}}]` erzwingt einen Zwischenhalt. Via == Ziel → `H9380`.                                                                            |
| `jnyFltrL` | `[{"type": "PROD", "mode": "INC", "value": "<bitmaske>"}]` — Verkehrsmittelfilter. Die Bitmaske ist **String**, nicht Zahl.                                        |

**Verkehrsmittel-Bitmaske** — das `cls`-Feld aus `common.prodL[]`, hier über den
kompletten Linienkatalog ([`LineSearch`](#linesearch--kompletter-linienkatalog))
ausgezählt:

| Bit | `catOut`             | Linien im Bestand |
|-----|----------------------|-------------------|
| 1   | S-Bahn               | 17                |
| 2   | Str (Stadtbahn)      | 86                |
| 8   | Bus, Midi, Mini, SEV | 2601              |
| 16  | RB, RE, Regio        | 111               |
| 32  | Fern, IC, ICE, THA   | 5                 |
| 128 | Fähre                | 1                 |
| 256 | AST, TAXI            | 304               |

4 und 64 kommen im KVB-Datenbestand nicht vor. Kombinieren per ODER: Stadtbahn

+ Bus = `"10"`. Ein Filter, der für die Strecke nichts übrig lässt, endet in
  `H890` ("keine Verbindung gefunden") — eine Antwort, kein Client-Fehler.

Client: `trip_search(..., num=, via_ext_id=, products=)` und `trip_page(…)`,
das zusätzlich `ctx_earlier`/`ctx_later` zurückgibt. Bitmasken-Konstanten in
`kvb_hafas.PRODUCTS`.

**Vollständige `req`-Feldliste**, aus dem Frontend (`grep -oE '"req\.[a-zA-Z.]*"'
hafas_lib_module_tp.js`): `depLocL`, `arrLocL`, `viaLocL[].loc` (+ `.min`),
`outDate`, `outTime`, `outFrwd`, `numF`, `maxChg`, `minChgTime`,
`chgTimeProfile`, `jnyFltrL` (`type`/`mode`/`value`/`meta`), `gisFltrL`,
`getPasslist`, `getPolyline`, `getTariff`, `getIV`, `getIST`, `getConGroups`,
`economic`, `liveSearch`, `ushrp`, `ctxScr` (Blättern), `ctxRecon`,
`outReconL[].ctx`, `storageId`, `psCtx`, `psOutReconL[].ctx`, `psSupplChgTime`,
`psInput.replacementSearch.{ctx,supplChgTime}`, `frontPreselectionL` /
`backPreselectionL` (`nodes[].loc`, `gisProfile.type`). Locations akzeptieren
`extId`, `lid`, `name`, `type`, `crd.x`/`crd.y`, `globalIdL`, `eteId`.

### Reconstruction — Verbindung wiederherstellen

```json
{
  "meth": "Reconstruction",
  "req": {
    "ctxRecon": "¶HKI¶T$A=1@O=Köln Neumarkt@L=300000203@a=128@$…"
  }
}
```

**Response**: aufgebaut wie `TripSearch`, aber mit **genau einer** Verbindung —
dieselbe Verbindung mit frischen Echtzeitdaten, ohne neue Suche.

- `outReconL: [{"ctx": "…"}]` ist eine gleichwertige Request-Form.

### SearchOnTrip — Alternativen zu einer Verbindung

```json
{
  "meth": "SearchOnTrip",
  "req": {
    "ctxRecon": "¶HKI¶T$A=1@O=Köln Neumarkt@L=300000203@a=128@$…"
  }
}
```

**Response**: `res.outConL[]` wie `TripSearch` — die ursprüngliche Verbindung
plus spätere Alternativen auf derselben Relation (im Test 12).

- `ctxRecon` ist das einzig nötige Feld; der Zeitbezug steckt im Kontext.
- `date`/`time` sind gültige Feldnamen, lassen den Request aber **immer** auf
  `PARSE` laufen. `sotMode: "RC"` ändert nichts, `sotMode: "JI"` mit `jid` →
  `DATE_TIME`/`FAIL`.

### GisRoute — Fußweg straßengenau

Kein freies A-nach-B-Routing, sondern die Lupe auf einen Fußweg, den HAFAS schon
vorgeschlagen hat. Eingabe `gisCtx` = `gis.ctx` eines `WALK`-Abschnitts aus
`TripSearch`.

```json
{
  "meth": "GisRoute",
  "req": {
    "gisCtx": "H|1|W$A=1@O=Köln Zollstock Südfriedhof@L=300005701@a=128@$…|#VE#2#CF#100#…",
    "getPolyline": true
  }
}
```

**Response**: `res.conL[0]` — ein `WALK`-Abschnitt mit `gis.dist` (Meter) und
`dur`, dazu `common.polyL[0].crdEncYX` als Google-Polyline (148 m Fußweg →
5 Stützpunkte, echte Straßengeometrie).

- ⚠️ **Selbstgebaute `gisCtx`-Strings werden abgelehnt** (`FAIL`) — Steig-IDs,
  Master-IDs, Koordinaten, mit und ohne `|#VE#…#`-Suffix. Nur vom Server selbst
  ausgegebene Token funktionieren; freies Fußweg-Routing ist nicht drin.
- Gültige Felder: `gisCtx`, `date`, `time`, `depLoc`, `arrLoc` (**Singular** —
  Plural-Formen sind `HAMM`), `gisFltrL`, `getPolyline`, `getDescription`,
  `getEco`. Der Weg über `depLoc`/`arrLoc` kommt nie über `PARAMETER` hinaus.

### HimSearch — Störungsmeldungen

Leerer Filter liefert alle aktiven Meldungen netzweit:

```json
{
  "meth": "HimSearch",
  "req": {
    "himFltrL": []
  }
}
```

**Response** (`res.msgL[]`):

| Feld            | Bedeutung                                                                                                                                         |
|-----------------|---------------------------------------------------------------------------------------------------------------------------------------------------|
| `text`          | Meldungstext (Klartext, oft mit `(H)` für Haltestelle)                                                                                            |
| `cat`           | `1` = Aufzug/Fahrzeuge außer Betrieb, `3` = Baumaßnahme/Verlegung, `99` = **Marketing** (rausfiltern)                                             |
| `prio`          | Priorität                                                                                                                                         |
| `sDate`/`eDate` | Gültigkeitszeitraum (`YYYYMMDD`)                                                                                                                  |
| `fLocX`/`tLocX` | Index in `res.common.locL[]` — betroffene Haltestelle(n), falls vorhanden                                                                         |
| `prod`          | Verkehrsmittel-Bitmaske — **keine Linien-Angabe**; `common.prodL` ist leer, betroffene Linien stehen nur im Klartext und nur in ~8% der Meldungen |

**Filter nach Linie** funktioniert, mit dem Label wie auf dem Abfahrtsmonitor:

```json
{
  "meth": "HimSearch",
  "req": {
    "himFltrL": [
      {
        "type": "LINE",
        "mode": "INC",
        "value": "133"
      }
    ]
  }
}
```

- `mode: "INC"` ist Pflicht — ohne wird der Filter stillschweigend ignoriert.
- Unbekanntes Label (`"999"`, `"Bus 133"`) → leere `msgL`, kein Fehler.
- Funktionierende `type`-Werte: `LINE`, `PROD` (Verkehrsmittel-Bitmaske),
  `REG` (numerisch) und der leere Filter. `LINEID` und `STATION` → `PARSE`.
- Weitere akzeptierte Felder: `maxNum`, `dateB`/`dateE`, `timeB`/`timeE`,
  `onlyToday`, `onlyHimId`. `sortL` und `getPolyline` sind gültige Namen, lösen
  aber `PARSE` aus. Rückwärts geht nichts, siehe [Historische Daten](#historische-daten).

⚠️ **Kein serverseitiger Filter nach Haltestelle.** Client-seitig (so macht es
`service_alerts(stop)`):

1. **Loc-Referenzen**: `fLocX`/`tLocX` der Meldung plus die der über `eventRefL`
   verlinkten `common.himMsgEventL`-Einträge, aufgelöst gegen `common.locL`.
   Achtung: `locL` hat je Haltestelle einen Steig-Eintrag (`300xxxxxx`) **und**
   über `mMastLocX` einen Master (`900xxxxxx`) — nur letzterer entspricht der
   `extId` aus `find_stops`.
2. **Textabgleich**: ~2/3 der Meldungen (v.a. Aufzug/Baustelle) haben *keine*
   Loc-Referenz und nennen die Haltestelle nur im Klartext (`"(H) Ulrepforte"`).
   Daher vom Haltestellennamen führende Wörter (Stadt/Stadtteil) abschneiden, bis
   der Rest im Text vorkommt; kurze Ein-Wort-Reste (`"Str."`) verwerfen.
3. Koordinaten für Meldungen kommen aus denselben Loc-Referenzen plus
   `himMsgEdgeL[].icoCrd` (68 von 87 Meldungen hatten so einen Geo-Bezug).

### HimGeoPos — Störungen im Kartenausschnitt

```json
{
  "meth": "HimGeoPos",
  "req": {
    "rect": {
      "llCrd": {
        "x": 6750000,
        "y": 50830000
      },
      "urCrd": {
        "x": 7150000,
        "y": 51050000
      }
    }
  }
}
```

- ⚠️ Antwortet `OK`, war für Köln aber in allen Tests **leer** — hier tauchen nur
  Meldungen mit echtem Geo-Bezug auf. Praktikabler Weg: Koordinaten aus
  [`HimSearch`](#himsearch--störungsmeldungen) selbst.
- `getPolys`, `maxNum` → `HAMM`.

### HimMatch — aktuell betroffene Haltestellen

```json
{
  "meth": "HimMatch",
  "req": {}
}
```

**Response**: `res.affStL[]` — Haltestellen mit aktuell anliegender Störung, als
Steig-Einträge (`300xxxxxx`), ohne Koordinaten (`crd` ist `{x: 0, y: 0}`) und mit
Dubletten pro Steig. Im Test 1–3 Haltestellen, während `HimSearch` 86 Meldungen
listete.

- **Nimmt kein einziges Feld an** — `himFltrL`, `input`, `maxNum`, `date`, `locL`
  und alles andere → `HAMM`.

### LineMatch / LineDetails — Linien

```json
{
  "meth": "LineMatch",
  "req": {
    "input": "18"
  }
}
```

**Response** (`res.lineL[]`): `{lineId, prodX}`, Produkt in `common.prodL[]`.
Weitere Felder wie `type: "S"` → `HAMM`.

- ⚠️ Der Datenbestand geht **weit über die KVB hinaus**: `"1"` trifft auch
  `de:aac:…`, `de:vrr:…`, `nl:ln:…`. Köln/Bonn ist das Präfix `de:vrs:`.

```json
{
  "meth": "LineDetails",
  "req": {
    "lineId": "de:vrs:18"
  }
}
```

**Response**: `res.common.prodL[0]` mit `prodCtx` (`catOut` `"Str"`/`"Bus"`,
`line`, `lineId`), `oprX` → Betreiber in `common.opL`, und einem `stat`-Block:

| Feld      | Bedeutung                                   | KVB-Wert (Linie 18)     |
|-----------|---------------------------------------------|-------------------------|
| `cnt`     | Fahrten im Fahrplan                         | 1062                    |
| `cncl`    | Ausfälle                                    | 0                       |
| `ont`     | pünktliche Fahrten                          | 0                       |
| `rt`      | Fahrten mit Echtzeitdaten                   | 0                       |
| `him`     | Fahrten mit Störungsmeldung                 | 0                       |
| `delGrpL` | Minuten-Grenzen des Verspätungs-Histogramms | `[1,2,3,…,10,15,20,30]` |
| `delCntL` | Anzahl Fahrten pro Grenze                   | alles 0                 |

- Nur die volle `lineId` funktioniert — `"18"` → `FAIL`. `date`, `getStopL`,
  `getPolyline` → `HAMM`.
- Das Schema für eine Pünktlichkeitsstatistik ist da, aber außer `cnt` füllt die
  KVB nichts.

### LineSearch — kompletter Linienkatalog

```json
{
  "meth": "LineSearch",
  "req": {}
}
```

**Response** (`res.lineL[]`): `{lineId, prodX, locX}` für **den gesamten
Datenbestand** — 3125 Linien, davon 710 mit Präfix `de:vrs:` (Köln/Bonn). Ein
einziger Request, kein Suchbegriff nötig; `locX` zeigt auf eine Referenz-
Haltestelle in `common.locL`. Das ist die Liste, die `LineList` & Co. nicht
liefern (→ `HAMM`).

Client: `all_lines(prefix="de:vrs")`.

### LineGeoPos — Linien im Umkreis

```json
{
  "meth": "LineGeoPos",
  "req": {
    "ring": {
      "cCrd": {
        "x": 6948329,
        "y": 50935667
      },
      "maxDist": 300
    }
  }
}
```

**Response** (`res.lineL[]`): `{lineId, prodX, locX, jnyL}` — je Linie ein Eintrag
plus Beispielfahrten mit `stopL`.

- `ring` **oder** `rect`. `date`/`time`/`jnyFltrL` werden angenommen, lösen aber
  `PARAMETER` aus; `maxLoc`, `maxLine`, `getPolyline`, `onlyRT` → `HAMM`.
- **Deckel bei 50 Linien** pro Anfrage, unabhängig von der Rechteckgröße — für
  ein ganzes Netz kacheln.
- Vollständiger als `LocDetails.pRefL`: Neumarkt 14 statt 9 Linien, die
  Nachtlinien (`101`, `107`, `109`, `172`, `173`) fehlen in `pRefL`.

### ServerInfo — Fahrplanperiode

```json
{
  "meth": "ServerInfo",
  "req": {}
}
```

**Response**: `fpB`/`fpE` = Anfang/Ende der aktuellen Fahrplanperiode (`20251214`–`20261212`), `sD`/`sT` = Serverdatum
und -zeit.

## Methoden-Inventar

Ein unbekannter Methodenname antwortet mit `HAMM` — damit lässt sich trennen, was
existiert. Jeder andere Code (`OK`, `NULLPTR`, `PARAMETER`, `LOCATION`,
`DATE_TIME`, `DEPARTURE`, `TARIFF`, `PARSE`, `ERROR`, `FAIL`) heißt: Methode
existiert, Request war nur unvollständig.

**47 Methoden existieren nachweislich.** Alle Zeilen unten sind live geprüft (je ein Request mit leerem `req`). Drei
Mengen, die sich nicht decken:

- **Client** — wird von diesem Python-Client aufgerufen (21).
- **Frontend** — Name steht als Literal in den `hafas_lib_module_*.js` der
  KVB-WebApp (40). `LineSearch` steht dort, wird im normalen Betrieb aber nie
  gefeuert; `JourneyCourse`, `SearchOnTrip`, `HimGeoPos`, `HimMatch`,
  `JourneyTree`, `GisSearch` und `HimDetails` stehen dort gar nicht und wurden
  nur durch Raten gefunden. Keine der beiden Quellen ist für sich vollständig.
- **Leerer `req`** — der Fehlercode auf `{"meth": "<X>", "req": {}}`, also der
  Existenznachweis.

| Methode                                                           | Leerer `req` | Client                                                 | Frontend | Anmerkung                                                                                  |
|-------------------------------------------------------------------|--------------|--------------------------------------------------------|----------|--------------------------------------------------------------------------------------------|
| [`LocMatch`](#locmatch--haltestellen-suchen)                      | `NULLPTR`    | find_stops()                                           | ✅       |                                                                                            |
| [`LocGeoPos`](#locgeopos--haltestellen-in-der-nähe)               | `PARAMETER`  | nearby_stops()                                         | ✅       |                                                                                            |
| [`LocDetails`](#locdetails--haltestelle-im-detail)                | `OK`         | stop_details(), stop_lines()                           | ✅       |                                                                                            |
| [`LocGeoReach`](#locgeoreach--isochrone)                          | `LOCATION`   | reachable_stops()                                      | ✅       |                                                                                            |
| `LocSearch`                                                       | `PARAMETER`  | —                                                      | ✅       | Neben `LocMatch`. Felder nicht durchprobiert.                                              |
| `LocGraph`                                                        | `PARAMETER`  | —                                                      | ✅       | Graph-Darstellung, Schema offen.                                                           |
| [`StationBoard`](#stationboard--abfahrtstafel)                    | `DEPARTURE`  | station_board()                                        | ✅       |                                                                                            |
| [`JourneyDetails`](#journeydetails--einzelfahrt-im-detail)        | `PARAMETER`  | journey_details(), journey_route(), journey_segments() | ✅       |                                                                                            |
| [`JourneyMatch`](#journeymatch--fahrten-nach-linie)               | `PARAMETER`  | find_journeys()                                        | ✅       |                                                                                            |
| [`JourneyGeoPos`](#journeygeopos--live-fahrzeugpositionen)        | `FAIL`       | vehicle_positions()                                    | ✅       |                                                                                            |
| [`JourneyCourse`](#journeycourse--linienverlauf-als-polyline)     | `PARAMETER`  | journey_course()                                       | —        |                                                                                            |
| `JourneyTree`                                                     | `OK`         | —                                                      | —        | `OK` mit leerem `jnyTreeNodeL`; jede Parameter-Variante → `HAMM`.                          |
| `JourneyGraph`                                                    | `PARAMETER`  | —                                                      | ✅       | Graph-Darstellung, Schema offen.                                                           |
| [`TripSearch`](#tripsearch--verbindungssuche)                     | `LOCATION`   | trip_search()                                          | ✅       |                                                                                            |
| [`Reconstruction`](#reconstruction--verbindung-wiederherstellen)  | `PARAMETER`  | reconstruct()                                          | ✅       |                                                                                            |
| `ReconstructionContextConverter`                                  | `PARSE`      | —                                                      | ✅       | Kurzlink-/QR-Umwandlung, Storage-Dienst bei der KVB aus — siehe Tabelle unten.             |
| [`SearchOnTrip`](#searchontrip--alternativen-zu-einer-verbindung) | `PARAMETER`  | trip_alternatives()                                    | —        |                                                                                            |
| `PartialSearch`                                                   | `PARAMETER`  | —                                                      | ✅       | Abschnitt früher/später suchen; braucht `psCtx`, das die KVB nie ausliefert — siehe unten. |
| [`GisRoute`](#gisroute--fußweg-straßengenau)                      | `DATE_TIME`  | walk_route()                                           | ✅       |                                                                                            |
| `GisSearch`                                                       | `PARSE`      | —                                                      | —        | Existiert (`PARSE` statt `HAMM`), nimmt aber kein Feld an.                                 |
| [`HimSearch`](#himsearch--störungsmeldungen)                      | `OK`         | service_alerts()                                       | ✅       |                                                                                            |
| [`HimGeoPos`](#himgeopos--störungen-im-kartenausschnitt)          | `OK`         | alerts_in_area()                                       | —        |                                                                                            |
| [`HimMatch`](#himmatch--aktuell-betroffene-haltestellen)          | `OK`         | affected_stops()                                       | —        |                                                                                            |
| `HimDetails`                                                      | `OK`         | —                                                      | —        | `OK` mit leeren Listen, nimmt kein Feld an — auch nicht `hid` aus `HimSearch`.             |
| [`LineMatch`](#linematch--linedetails--linien)                    | `PARAMETER`  | find_lines()                                           | ✅       |                                                                                            |
| [`LineDetails`](#linematch--linedetails--linien)                  | `PARAMETER`  | line_details()                                         | ✅       |                                                                                            |
| [`LineGeoPos`](#linegeopos--linien-im-umkreis)                    | `PARAMETER`  | lines_in_area()                                        | ✅       |                                                                                            |
| [`LineSearch`](#linesearch--kompletter-linienkatalog)             | `OK`         | all_lines()                                            | ✅       |                                                                                            |
| [`ServerInfo`](#serverinfo--fahrplanperiode)                      | `OK`         | server_info()                                          | ✅       |                                                                                            |
| `TariffSearch`                                                    | `TARIFF`     | —                                                      | ✅       | Nimmt kein Feld an. Preise kommen ohnehin über `TripSearch.trfRes`.                        |
| `EventLocGeoPos`                                                  | `PARSE`      | —                                                      | ✅       | Veranstaltungsorte im Umkreis.                                                             |
| `GeoFeatureGeoPos`                                                | `PARSE`      | —                                                      | ✅       | Kartenobjekte/Layer-Geometrien.                                                            |
| `GeoFeatureDetails`                                               | `PARSE`      | —                                                      | ✅       | Detail zu einem Kartenobjekt.                                                              |
| `DataStoreLoad`                                                   | `PARSE`      | —                                                      | ✅       | Generischer Store-Zugriff.                                                                 |
| `ShareTrip`                                                       | `PARSE`      | —                                                      | ✅       | Schreibend (Verbindung teilen), nicht angefasst.                                           |
| `ShareLocation`                                                   | `PARSE`      | —                                                      | ✅       | Schreibend (Ort teilen), nicht angefasst.                                                  |
| `SubscrCreate`                                                    | `ERROR`      | —                                                      | ✅       | Push-Abo, braucht registrierten Nutzer. Schreibend, nicht angefasst.                       |
| `SubscrSearch`                                                    | `ERROR`      | —                                                      | ✅       | Push-Abo, braucht registrierten Nutzer. Schreibend, nicht angefasst.                       |
| `SubscrDetails`                                                   | `ERROR`      | —                                                      | ✅       | Push-Abo, braucht registrierten Nutzer. Schreibend, nicht angefasst.                       |
| `SubscrUpdate`                                                    | `ERROR`      | —                                                      | ✅       | Push-Abo, braucht registrierten Nutzer. Schreibend, nicht angefasst.                       |
| `SubscrDelete`                                                    | `ERROR`      | —                                                      | ✅       | Push-Abo, braucht registrierten Nutzer. Schreibend, nicht angefasst.                       |
| `SubscrUserCreate`                                                | `ERROR`      | —                                                      | ✅       | Push-Abo, braucht registrierten Nutzer. Schreibend, nicht angefasst.                       |
| `SubscrUserDetails`                                               | `ERROR`      | —                                                      | ✅       | Push-Abo, braucht registrierten Nutzer. Schreibend, nicht angefasst.                       |
| `SubscrUserUpdate`                                                | `ERROR`      | —                                                      | ✅       | Push-Abo, braucht registrierten Nutzer. Schreibend, nicht angefasst.                       |
| `SubscrUserDelete`                                                | `ERROR`      | —                                                      | ✅       | Push-Abo, braucht registrierten Nutzer. Schreibend, nicht angefasst.                       |
| `SubscrChannelConfirm`                                            | `ERROR`      | —                                                      | ✅       | Push-Abo, braucht registrierten Nutzer. Schreibend, nicht angefasst.                       |
| `SubscrChannelSendDetails`                                        | `ERROR`      | —                                                      | ✅       | Push-Abo, braucht registrierten Nutzer. Schreibend, nicht angefasst.                       |

**Die sechs interessanten Sackgassen im Detail** — Methoden, deren Schema wir kennen oder gezielt geknackt haben, die
trotzdem nichts liefern:

| Methode                          | Stand                                                                                                                                                                                                                                                                                                                                                                                                                |
|----------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `JourneyTree`                    | Leerer `req` → `OK` mit leerem `jnyTreeNodeL`; jede Parameter-Variante → `HAMM`.                                                                                                                                                                                                                                                                                                                                     |
| `TariffSearch`                   | Leerer `req` → `TARIFF`, nimmt aber **kein einziges Feld** an (`ctxRecon`, `conL`, `depLocL`, `ovwTrfRefL` → `HAMM`). Egal — Preise kommen über [`TripSearch`](#tripsearch--verbindungssuche).                                                                                                                                                                                                                       |
| `PartialSearch`                  | Schema aus dem Frontend bekannt (`psOutReconL[].ctx`, `psCtx`, `psInput.replacementSearch.ctx`, `psSupplChgTime`, `getPasslist`, `getPolyline`), trotzdem tot: `psCtx` erwartet ein `secL[].dep.psCtxDepL` bzw. `arr.psCtxArrE`, und **die KVB liefert diese Felder in keiner `TripSearch`-Antwort** (auch nicht mit `getConGroups`, `ushrp`, `getIV`, `getIST`, `economic`, `liveSearch`). Jeder Versuch → `PARSE`. |
| `ReconstructionContextConverter` | Schema bekannt: `{"mode": "TO_STORAGE_ID", "ctxL": ["<ctxRecon>"]}` → `res.storageIdL[]`, gedacht für Kurzlinks/QR-Codes. `TO_STORAGE_ID` ist der einzige gültige `mode` (alle anderen → `HAMM`) und antwortet immer `PARSE`. `Reconstruction` nimmt das Gegenstück `storageId` an — der Storage-Dienst ist bei der KVB nicht konfiguriert (kein `qrCodeBasePath` in der `webapp.config.json`).                      |
| `HimDetails`                     | Leerer `req` → `OK` mit leeren Listen, nimmt kein einziges Feld an (auch nicht `hid` aus `HimSearch`). Gleiche Sackgasse wie `JourneyTree`.                                                                                                                                                                                                                                                                          |
| `GisSearch`                      | Leerer `req` → `PARSE` (existiert also), aber kein Feld akzeptiert, `req` als Liste/String ebenfalls `HAMM`.                                                                                                                                                                                                                                                                                                         |

**Nicht vorhanden** (alle `HAMM`): `StopList`, `StationList`, `LocList`,
`LineList`, `ArchiveSearch`, `HistorySearch`, `JourneyArchive`, `IstDaten`,
`StatisticsSearch`, `Punctuality`, `DelaySearch`, `JourneyStatus`, `NetworkInfo`,
`ScheduleInfo`, `CalendarInfo`, `VehicleGeoPos`, `MapLayers`, `UserInfo`, `Ping`,
`StationBoardTree`, `TimetableInfo`, `PoiSearch`, `Themes`, `NearbySearch`,
`Geometry`, `FareSearch`, `Ticket`, `PriceSearch`, `BestPrice`, `LocData`,
`GisLocation`, `MatchSvc`, `Departure`, `Arrival`, `Kaleidoscope`,
`SubscrChannelList`, `AttrSearch`, `OperatorSearch`, `ProductSearch`,
`CalendarSearch`, `CheckIn`, `AddOnSearch`, `BookingDetails`, `ConGrpSettings`,
`GisInfo`, `GisMatch`, `GisGeoPos`, `GisLocDetails`, `Rebook`,
`MatchServiceDays`, `JourneyFilter`, `JourneyFilterMatch`, `OTPDetails`,
`TripInfo`, `TripPrice`, `LocValidate`, `PoiGeoPos`, `NetSummary`, `GraphInfo`,
`TimetableChange`, `ConDetails`, `ConScoreSearch`, `HimMatchGeo`, `ProdMatch`,
`OperatorMatch`, `AttrMatch`, `RemarkSearch`, `IconSearch`, `ThemeMatch`,
`UserSettings`, `ClientSettings`, `FeedbackCreate`, `MapData`, `Layers`,
`LineFilter`, `MatchLoc`, `StationBoardSearch`, `TrainSearch`.

**Rezept zum Methoden-Finden — nicht raten, das Frontend lesen.** Die HAFAS-WebApp
lädt ihre Features als einzelne Module nach; `hafas_lib_core.js` listet sie in
`addModule("<name>")`, jedes liegt unter
`https://auskunft.kvb.koeln/js/hafas_lib_module_<name>.js` (36 Stück, u.a.
`livemap`, `mobilityradar`, `tariffwizard`, `elevationlevel`). Die
Methodennamen stehen dort als Literale:

```bash
curl -s https://auskunft.kvb.koeln/js/hafas_lib_module_tp.js > tp.js
grep -ohE 'hafasHCIRequestObject"\s*,\s*"[A-Za-z]+"|request:"[A-Za-z]+"' *.js | sort -u
```

Dasselbe gilt für Request-Felder: die Module bauen ihre Requests über
`r.add("req.<feld>", …)`, ein `grep -oE '"req\.[a-zA-Z.]*"'` liefert die
vollständige Feldliste einer Methode, inklusive der nie benutzten. So fielen
`LineSearch`, `PartialSearch`, `ReconstructionContextConverter`, `LocSearch`,
`JourneyGraph`, `LocGraph`, `EventLocGeoPos`, `GeoFeature*` und `DataStoreLoad`
auf einen Schlag.

**Gegenprobe:** 231 systematisch erzeugte Namen (Präfixe `Loc|Journey|Line|Him|
Geo|GeoFeature|Event|Trip|Con|Gis|Tariff|Stc|Poly|Op|Prod|Rem|Map` × Suffixe
`Match|Details|GeoPos|Search|Graph|Course|Tree|Reach|Data|GeoReach|List|Info|
Load|Store|Pos`) ergaben genau **einen** Treffer (`LineSearch`, den das
Frontend ohnehin nannte). Der Namensraum ist damit weitgehend ausgeschöpft:
**47 Methoden existieren nachweislich** (siehe Tabelle oben), 21 davon nutzt
dieser Client.

**Rezept zum Schema-Knacken:** `HAMM` sagt nicht, *welches* Feld schuld ist —
also pro Request **genau ein Feld** schicken. `HAMM` = Feldname existiert nicht,
jeder andere Fehler = Feld akzeptiert. So fällt die gültige Feldliste in ~20
Requests raus; danach nur noch die Überlebenden kombinieren. So fielen `GisRoute`
(`gisCtx`) und `SearchOnTrip` (`ctxRecon`).

## Historische Daten

**Kein Archiv, auf keiner Methode.**

- **Störungsmeldungen**: `HimSearch` akzeptiert `dateB`/`dateE`, und die Filter
  wirken (verschiedene Fenster → verschiedene Trefferzahlen), aber in keinem
  getesteten Fenster (Dez 2025, Jan 2026, 2024) kam eine einzige **abgelaufene**
  Meldung zurück. Der HIM-Speicher hält nur gültige und künftige Meldungen; die
  Datumsfelder filtern innerhalb dieses lebenden Bestands.
- **Archiv-Methoden**: `ArchiveSearch`, `HistorySearch`, `JourneyArchive`,
  `IstDaten`, `StatisticsSearch`, `Punctuality`, `DelaySearch` → alle `HAMM`.
  Die einzige aggregierte Kennzahl wäre der `stat`-Block von
  [`LineDetails`](#linematch--linedetails--linien), den die KVB nicht füllt.
- **Fahrplan der Vergangenheit**: `StationBoard` mit `date` geht rückwärts nur
  innerhalb der aktuellen Fahrplanperiode (ab ~14.12.2025; davor `H9360`) — und
  liefert dort nur `dTimeS`, kein `dTimeR`. Also nur der **Fahrplan**, nicht was
  tatsächlich passiert ist.

Echte Verlaufsdaten gehen nur außerhalb dieser API: selbst sammeln (`StationBoard` pollen, Ist-Werte + `isCncl`
persistieren) oder die KVB direkt
nach internen SAE/ITCS-Daten fragen.

## Auslastungsdaten

**Nicht gefunden.** Weder `StationBoard` noch `JourneyDetails` liefern ein
Auslastungs-/Kapazitätsfeld (in anderen HAFAS-Installationen z.B. `occ`). Die
`TripSearch`-Antwort bringt die passenden `common`-Blöcke (`tcocL`, `stcGrpL`,
`stcLiL`, `tctcL`) zwar mit, die KVB füllt sie aber alle leer.

## Bekannte Fehlercodes

| Code                                                         | Bedeutung                                                                |
|--------------------------------------------------------------|--------------------------------------------------------------------------|
| `OK`                                                         | Erfolg                                                                   |
| `H9360`                                                      | Datum außerhalb der gültigen Fahrplanperiode                             |
| `PARSE`                                                      | Fehlerhafter Request-Body (Top-Level `err`, nicht in `svcResL`)          |
| `HAMM`                                                       | Unbekannte Methode **oder** unbekanntes/falsch typisiertes Feld im `req` |
| `NULLPTR`, `PARAMETER`, `LOCATION`, `DATE_TIME`, `DEPARTURE` | Methode existiert, Pflichtparameter fehlt                                |
| `FAIL`                                                       | Generischer Fehler                                                       |

## Herkunft

1. `kvb.koeln/fahrtinfo/widget-generator/` → `auskunft.kvb.koeln/widgetgenerator.html`
2. lädt `js/hafas_webapp_config.js` → `_.externalConfigPath = "config/webapp.config.json"`
3. `config/webapp.config.json` enthält `"urlMgate": "https://auskunft.kvb.koeln/gate"`
   und die `aid`. Sonst nichts Brauchbares — keine weiteren Service-URLs,
   HaCon-Cookie-Links, leerer Maps-API-Key. Build-Datum: 29. August 2022.
4. Ab da Standard-HAFAS-`mgate`, dokumentiert u.a. in
   [hafas-client](https://github.com/public-transport/hafas-client) und
   [derf's EFA/HAFAS-Notizen](https://finalrewind.org/interblag/entry/efa-json-api/).

`auskunft.kvb.koeln` ist ein reiner mgate-Host: `/bin/mgate.exe`, `/restproxy`,
`/hafas-proxy`, `/gis/gate`, `/version.json`, `/api/`, `/rest/`, `/hafasRESTful/`,
`/gtfs/`, `/opendata/`, `/tiles/` u.a. → alle `404`, `/gate/` → `400`. Es gibt
genau einen Endpoint.

Keine Zugangsdaten umgangen — alles hier ist aus öffentlich ausgeliefertem
Frontend-Code ableitbar. Siehe README.md für den rechtlichen Disclaimer.
