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
  Mehrere `svcReqL`-Einträge werden in einer Response batch-weise beantwortet.
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
existiert. `NULLPTR`/`PARAMETER`/`LOCATION`/`DATE_TIME`/`DEPARTURE` heißt
umgekehrt: Methode existiert, Pflichtparameter fehlt.

**Vorhanden und genutzt:** `LocMatch`, `LocGeoPos`, `LocDetails`, `LocGeoReach`,
`StationBoard`, `JourneyDetails`, `JourneyMatch`, `JourneyGeoPos`,
`JourneyCourse`, `TripSearch`, `Reconstruction`, `SearchOnTrip`, `GisRoute`,
`HimSearch`, `HimGeoPos`, `HimMatch`, `LineMatch`, `LineDetails`, `LineGeoPos`,
`ServerInfo`.

**Vorhanden, Request-Schema nicht geknackt:**

| Methode        | Stand                                                                                                                                                                  |
|----------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `JourneyTree`  | Leerer `req` → `OK` mit leerem `jnyTreeNodeL`; jede Parameter-Variante → `HAMM`.                                                                                       |
| `TariffSearch` | Leerer `req` → `TARIFF`, nimmt aber **kein einziges Feld** an (`ctxRecon`, `conL`, `depLocL`, `ovwTrfRefL` → `HAMM`). Einzige Preis-Methode der API, und unerreichbar. |
| `Subscr*`      | `SubscrCreate`, `SubscrSearch`, `SubscrDetails`, `SubscrUserCreate` → `ERROR` statt `HAMM`; brauchen vermutlich einen registrierten Nutzer (Push-Abos).                |

**Nicht vorhanden** (alle `HAMM`): `StopList`, `StationList`, `LocList`,
`LineList`, `ArchiveSearch`, `HistorySearch`, `JourneyArchive`, `IstDaten`,
`StatisticsSearch`, `Punctuality`, `DelaySearch`, `JourneyStatus`, `NetworkInfo`,
`ScheduleInfo`, `CalendarInfo`, `VehicleGeoPos`, `MapLayers`, `UserInfo`, `Ping`,
`StationBoardTree`, `TimetableInfo`, `PoiSearch`, `Themes`, `NearbySearch`,
`Geometry`, `FareSearch`, `Ticket`, `PriceSearch`, `BestPrice`, `LocData`,
`GisLocation`, `MatchSvc`, `Departure`, `Arrival`, `Kaleidoscope`,
`SubscrChannelList`, `AttrSearch`, `OperatorSearch`, `ProductSearch`,
`CalendarSearch`, `CheckIn`.

Alles Tarif-/Preisbezogene fehlt damit komplett: Verbindungen enthalten zwar
`ovwTrfRefL`-Referenzen, aber keine Methode macht daraus Preise.

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
Auslastungs-/Kapazitätsfeld (in anderen HAFAS-Installationen z.B. `occ`). Nicht
abschließend verifiziert — falls es einen Weg gibt, dann über eine noch nicht
durchprobierte `req`-Option bei `StationBoard`.

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
