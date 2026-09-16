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
    - [Reconstruction — Verbindung wiederherstellen](#reconstruction--verbindung-wiederherstellen)
    - [SearchOnTrip — Alternativen zu einer Verbindung](#searchontrip--alternativen-zu-einer-verbindung)
    - [GisRoute — Fußweg straßengenau](#gisroute--fußweg-straßengenau)
    - [HimSearch — Störungsmeldungen](#himsearch--störungsmeldungen)
    - [HimGeoPos — Störungen im Kartenausschnitt](#himgeopos--störungen-im-kartenausschnitt)
    - [ServerInfo — Fahrplanperiode](#serverinfo--fahrplanperiode)
    - [LocDetails — Haltestelle im Detail](#locdetails--haltestelle-im-detail)
    - [LocGeoReach — Isochrone](#locgeoreach--isochrone)
    - [JourneyGeoPos — Live-Fahrzeugpositionen](#journeygeopos--live-fahrzeugpositionen)
    - [JourneyMatch — Fahrten nach Linie](#journeymatch--fahrten-nach-linie)
    - [JourneyCourse — Linienverlauf als Polyline](#journeycourse--linienverlauf-als-polyline)
    - [LineMatch / LineDetails — Linien](#linematch--linedetails--linien)
- [Methoden-Inventar](#methoden-inventar)
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
      "req": {
        /* ... */
      }
    }
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
"auth": {
  "type": "AID",
  "aid": "Rt6foY5zcTTRXMQs"
}
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
      "loc": {
        "name": "Neumarkt?",
        "type": "S"
      },
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

⚠️ Liefert ohne zusätzlichen Filter **auch POIs** (Museen, Sehenswürdigkeiten
etc.), nicht nur Haltestellen — im Test kamen z.B. "Gürzenich" und
"Wallraf-Richartz-Museum" zurück. HAFAS unterstützt normalerweise einen
`locFltrL`-Parameter mit einer Produkt-Typ-Bitmaske, um auf ÖPNV-Haltestellen
einzuschränken; die korrekte Maske für KVB haben wir noch nicht verifiziert (ein Testwert `"1023"` führte zu keiner
sichtbaren Filterung).

### StationBoard — Abfahrtstafel

Die Kernmethode für Echtzeit-Abfahrten.

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

- `type`: `"DEP"` (Abfahrten) oder `"ARR"` (Ankünfte).
- `date`/`time` optional (Format `YYYYMMDD` / `HHMMSS`) — ohne Angabe wird
  "jetzt" angenommen. Auch für Zukunft/Vergangenheit innerhalb der aktuellen
  Fahrplanperiode nutzbar, siehe [Historische Daten](#historische-daten).

**Response** (`res.jnyL[]`), pro Journey u.a.:

| Feld                          | Bedeutung                                                                                   |
|-------------------------------|---------------------------------------------------------------------------------------------|
| `stbStop.dTimeS`              | geplante Abfahrtszeit (Soll)                                                                |
| `stbStop.dTimeR`              | Echtzeit-Prognose (Ist) — fehlt bei rein geplanten/vergangenen Fahrten                      |
| `stbStop.dPlatfS` / `dPlatfR` | Gleis/Bahnsteig, Soll/Ist                                                                   |
| `prodX`                       | Index in `res.common.prodL[]` → dort `name` = Linienbezeichnung (z.B. "146")                |
| `dirTxt`                      | Zielhaltestelle/Richtungstext                                                               |
| `isCncl`                      | `true` bei Ausfall (im Test durchweg `false` — bei echten Ausfällen noch nicht verifiziert) |
| `jid`                         | Journey-ID, Eingabe für `JourneyDetails`                                                    |

### JourneyDetails — Einzelfahrt im Detail

Alle Zwischenhalte einer einzelnen Fahrt, inkl. Soll/Ist-Zeiten pro Halt.

```json
{
  "meth": "JourneyDetails",
  "req": {
    "jid": "1|2100|1|1|15092026"
  }
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

**Response** (`res.outConL[]`): Verbindungen mit `dep`/`arr` (Zeiten) und
`secL[]` (einzelne Teilstrecken/Umstiege). Im Test kamen z.B. 3 Verbindungen
für eine simple Direktstrecke zurück (unterschiedliche Abfahrtszeiten).

### Reconstruction — Verbindung wiederherstellen

Jede Verbindung aus `TripSearch` bringt ein `ctxRecon`-Token mit. Damit lässt
sich genau diese Verbindung später erneut abfragen — mit frischen
Echtzeitdaten, ohne neue Suche.

```json
{
  "meth": "Reconstruction",
  "req": {
    "ctxRecon": "¶HKI¶T$A=1@O=Köln Neumarkt@L=300000203@a=128@$…"
  }
}
```

**Response**: identisch aufgebaut zu `TripSearch` (`res.outConL[]`), aber mit
genau einer Verbindung. `outReconL: [{"ctx": "…"}]` funktioniert als
alternative Request-Form und liefert dasselbe.

### SearchOnTrip — Alternativen zu einer Verbindung

Braucht **nur** das `ctxRecon`-Token einer Verbindung:

```json
{
  "meth": "SearchOnTrip",
  "req": {
    "ctxRecon": "¶HKI¶T$A=1@O=Köln Neumarkt@L=300000203@a=128@$…"
  }
}
```

**Response**: `res.outConL[]` wie bei `TripSearch`, im Test **12 Verbindungen**
— die ursprüngliche plus spätere Alternativen auf derselben Relation. Das ist
der Unterschied zu `Reconstruction`, das genau eine liefert.

`sotMode: "RC"` ändert nichts am Ergebnis, `sotMode: "JI"` mit `jid` statt
`ctxRecon` endet in `DATE_TIME`/`FAIL`. `date` und `time` sind zwar gültige
Feldnamen (kein `HAMM`), lassen den Request aber **immer** auf `PARSE`
laufen — egal ob als String oder Zahl. Der Zeitbezug steckt im Kontext.

### GisRoute — Fußweg straßengenau

Kein freies A-nach-B-Routing, sondern die Lupe auf einen Fußweg, den HAFAS
schon vorgeschlagen hat. Eingabe ist `gisCtx` — das `ctx`-Feld aus dem
`gis`-Block eines `WALK`-Abschnitts einer `TripSearch`:

```json
{
  "meth": "GisRoute",
  "req": {
    "gisCtx": "H|1|W$A=1@O=Köln Zollstock Südfriedhof@L=300005701@a=128@$…|#VE#2#CF#100#…",
    "getPolyline": true
  }
}
```

**Response**: `res.conL[0]` — eine Verbindung aus genau einem `WALK`-Abschnitt
mit `gis.dist` (Meter) und `dur`, dazu `common.polyL[0].crdEncYX` als
Google-Polyline. Im Test: 148 m Fußweg → 5 Stützpunkte, also echte
Straßengeometrie statt Luftlinie.

⚠️ **Selbstgebaute `gisCtx`-Strings werden abgelehnt** (`FAIL`). Getestet mit
Steig-IDs, Master-IDs und reinen Koordinaten, jeweils mit und ohne den
`|#VE#…#`-Suffix — der Server akzeptiert nur Token, die er selbst ausgegeben
hat. Freies Fußweg-Routing zwischen beliebigen Punkten ist damit nicht drin.

Gültige Felder laut Einzeltest: `gisCtx`, `date`, `time`, `depLoc`, `arrLoc`
(**Singular** — `depLocL`/`arrLocL` im Plural sind `HAMM`), `gisFltrL`,
`getPolyline`, `getDescription`, `getEco`. Der Weg über `depLoc`/`arrLoc`
kommt allerdings nie über `PARAMETER` hinaus, in keiner der getesteten
`gisFltrL`-Varianten — nur `gisCtx` führt zum Ziel.

### HimSearch — Störungsmeldungen

**Funktioniert — mit leerem Filter.** Der Trick: `himFltrL: []` (keine
Filter) liefert alle aktuell aktiven Meldungen netzweit, statt eines Fehlers:

```json
{
  "meth": "HimSearch",
  "req": {
    "himFltrL": []
  }
}
```

**Response** (`res.msgL[]`), pro Meldung u.a.:

| Feld            | Bedeutung                                                                                                                                                                             |
|-----------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `text`          | Meldungstext (Klartext, oft mit `(H)` für Haltestelle)                                                                                                                                |
| `cat`           | Kategorie: `1` = Aufzug/Fahrzeuge außer Betrieb, `3` = Baumaßnahme/Verlegung, `99` = **Marketing** (KVB-Werbung, kein Betriebshinweis — rausfiltern!)                                 |
| `prio`          | Priorität                                                                                                                                                                             |
| `sDate`/`eDate` | Gültigkeitszeitraum (Start/Ende, `YYYYMMDD`)                                                                                                                                          |
| `fLocX`/`tLocX` | Index in `res.common.locL[]` — betroffene Haltestelle(n), falls vorhanden                                                                                                             |
| `prod`          | Verkehrsmittel-Bitmaske (`8`, `266`, `0`) — **keine Linien-Angabe**; `res.common.prodL` ist leer, betroffene Linien stehen nur im Klartext ("Linie 133") und nur in ~8% der Meldungen |

**Filter nach Linie funktioniert** — `type: "LINE"` mit `mode: "INC"` und
dem Linien-Label wie auf dem Abfahrtsmonitor:

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

- Ohne `mode: "INC"` wird der Filter stillschweigend ignoriert (liefert
  wieder alle Meldungen) — `mode` ist Pflicht.
- Unbekanntes Label (`"999"`, `"Bus 133"`) → leere `msgL`, kein Fehler.
- Getestet: `"133"` → 25, `"142"` → 19 Meldungen (mit Schnittmenge, aber je
  10 eigenen). Die Stadtbahnlinien `1`/`7`/`9`/`18` lieferten identische
  22er-Sets — aktuell sind alle Stadtbahn-Meldungen netzweit, das ist der
  Datenstand, keine Filter-Schwäche.
- `type: "LINEID"` und `"STATION"` → `PARSE`-Fehler.

⚠️ **Kein funktionierender Filter nach Haltestelle** — alle getesteten
`himFltrL`-Varianten (`STATION`, mit `mode: "INC"` oder ohne) führten zu
einem `PARSE`-Fehler auf Envelope-Ebene (`res.svcResL` leer, `err` direkt
im Top-Level-Objekt statt in `svcResL[0]`). Neben `LINE` funktionieren nur
`PROD` (Verkehrsmittel-Bitmaske, `"8"` → 60 Meldungen), `REG` mit
numerischem Wert (`"1"`) und der leere Filter. Client-seitiges Filtern nach
Haltestelle ist der pragmatische
Workaround — `service_alerts(stop)` macht genau das:

1. **Loc-Referenzen**: `fLocX`/`tLocX` der Meldung plus die der über
   `eventRefL` verlinkten `common.himMsgEventL`-Einträge, aufgelöst gegen
   `common.locL`. Achtung: `locL` enthält pro Haltestelle einen
   Steig-Eintrag (`extId` `300xxxxxx`) **und** über `mMastLocX` einen
   Master-Eintrag (`extId` `900xxxxxx`) — nur letzterer entspricht der
   `extId` aus `find_stops`.
2. **Textabgleich**: Der Großteil der Meldungen (~2/3, v.a. Aufzugs- und
   Baustellenmeldungen) hat *gar keine* Loc-Referenz und nennt die
   Haltestelle nur im Klartext (`"(H) Ulrepforte"`). Daher zusätzlich
   Namensabgleich: vom Haltestellennamen werden führende Wörter (Stadt/Stadtteil, `"Köln Lindenthal Bachemer Str."`)
   abgeschnitten, bis
   der Rest im Text vorkommt. Kurze Ein-Wort-Reste (`"Str."`) werden
   verworfen, sonst matcht alles.

Gültige `type`-Werte laut einer Fehlermeldung bei falscher Groß-/
Kleinschreibung: `EID, SRC, DEPT, HIMID, TRAIN, HIMCAT, PID, HIMTAG, COMP,
TXT, OPR, LINE, SENDER, GLINEID, PROD, AFLD, HIMTXT, LINEID, STATION, CAT,
ADMIN, META, CH, UIC, REG` — welche davon tatsächlich ohne Parse-Fehler
funktionieren, ist noch nicht systematisch durchgetestet.

### HimGeoPos — Störungen im Kartenausschnitt

Der geografische Gegenentwurf zum fehlenden Haltestellen-Filter von
`HimSearch`: Meldungen in einer Bounding-Box.

```json
{
  "meth": "HimGeoPos",
  "req": {
    "rect": {
      "llCrd": {"x": 6750000, "y": 50830000},
      "urCrd": {"x": 7150000, "y": 51050000}
    }
  }
}
```

⚠️ Antwortet mit `OK`, war für Köln in allen Tests aber **leer** — hier
tauchen nur Meldungen mit echtem Geo-Bezug auf, und der Großteil der
KVB-Meldungen nennt die Haltestelle nur im Klartext. `getPolys` und `maxNum`
führen zu `HAMM`. Für die Praxis bleibt `service_alerts(stop=…)` (Textabgleich)
der verlässlichere Weg.

### ServerInfo — Fahrplanperiode

Kürzeste Methode der ganzen API, ohne Parameter:

```json
{"meth": "ServerInfo", "req": {}}
```

**Response**: `fpB`/`fpE` = Anfang/Ende der aktuellen Fahrplanperiode
(getestet am 2026-09-16: `20251214` – `20261212`), `sD`/`sT` = Serverdatum
und -zeit. Damit muss die Periodengrenze nicht mehr per Binärsuche über
`H9360`-Fehler ermittelt werden.

### LocDetails — Haltestelle im Detail

```json
{
  "meth": "LocDetails",
  "req": {
    "locL": [{"type": "S", "lid": "A=1@L=900000002@"}]
  }
}
```

Beachte die `lid`-Form `A=1@L=<extId>@` — ein blankes `{"extId": …}` reicht
hier nicht.

**Response** (`res.locL[0]`):

| Feld         | Bedeutung                                                              |
|--------------|------------------------------------------------------------------------|
| `pRefL`      | Indizes in `common.prodL[]` → **alle Linien, die den Halt bedienen**     |
| `stopLocL`   | Indizes aller Steige/Masten der Haltestelle                             |
| `entryLocL`  | Zugänge/Eingänge                                                        |
| `isMainMast` | `true` beim Master-Eintrag (extId `900xxxxxx`)                          |
| `wt`         | interner "weight" (Bedeutung der Haltestelle im Netz)                   |

`pRefL` ist die saubere Quelle für "welche Linien halten hier" — vorher hat
der Client dafür die Abfahrtstafel abgefragt und nur gesehen, was zufällig
als nächstes fährt. Achtung: `common.prodL` enthält **mehr** Linien als
`pRefL` referenziert (Neumarkt: 14 Produkte, 9 Referenzen) — nur die
referenzierten gelten.

### LocGeoReach — Isochrone

Alle Haltestellen, die von einem Startpunkt aus in X Minuten erreichbar sind.

```json
{
  "meth": "LocGeoReach",
  "req": {
    "loc": {"type": "S", "lid": "A=1@L=900000002@"},
    "maxDur": 15,
    "maxChg": 0,
    "date": "20260916",
    "time": "120000"
  }
}
```

**Response** (`res.posL[]`): pro Eintrag `locX` (Index in `common.locL`),
`dur` (Minuten), `chg` (Umstiege), `prodX`, `lastLocX`. `date`/`time` sind
optional.

⚠️ `posL` zeigt auf **Steig-Einträge** (extId `300xxxxxx`), nicht auf
Haltestellen — Neumarkt/15 min/0 Umstiege ergab 132 Einträge, die sich auf
deutlich weniger echte Haltestellen verteilen. Über `mMastLocX` auf den
Master (`900xxxxxx`) auflösen und pro Haltestelle den schnellsten Eintrag
behalten; `reachable_stops()` macht genau das.

`getPoly: true` wurde nicht akzeptiert (`HAMM`) — keine Isochronen-Fläche,
nur die Haltestellenliste.

### JourneyGeoPos — Live-Fahrzeugpositionen

Die interessanteste bisher unentdeckte Methode: alle Fahrzeuge in einer
Bounding-Box, mit Position.

```json
{
  "meth": "JourneyGeoPos",
  "req": {
    "maxJny": 100,
    "onlyRT": false,
    "rect": {
      "llCrd": {"x": 6900000, "y": 50880000},
      "urCrd": {"x": 7020000, "y": 50990000}
    },
    "perSize": 120000,
    "perStep": 30000,
    "ageOfReport": true,
    "trainPosMode": "CALC"
  }
}
```

**Response** (`res.jnyL[]`): zusätzlich zu den üblichen Journey-Feldern

| Feld    | Bedeutung                                                                                                |
|---------|----------------------------------------------------------------------------------------------------------|
| `pos`   | aktuelle Position `{x, y}` (wie immer `* 1_000_000`)                                                       |
| `ani`   | Animations-Track: `mSec[]` (Offsets in ms, hier 0/30k/60k/90k/120k), `proc[]` (Fortschritt zwischen zwei Halten in ‰), `dirGeo[]`, `fLocX[]`/`tLocX[]` — gedacht für flüssige Karten-Animation ohne Nachpollen |
| `stopL` | Halte der Fahrt mit Soll/Ist-Zeiten                                                                        |

`trainPosMode: "CALC"` heißt: HAFAS **rechnet** die Position aus Fahrplan
plus Echtzeit-Prognose hoch — keine GPS-Rohdaten. `perSize`/`perStep`
steuern Länge und Auflösung des Animations-Tracks. Die Box liefert alles im
Bediengebiet, also auch Regionalbusse und -bahnen anderer Betreiber.

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

`date` **und** `time` sind Pflicht — ohne sie: `FAIL`.

**Response** (`res.jnyL[]`): Fahrten mit `jid`, `stopL` (nur erster und
letzter Halt), `pos` und vor allem `sDaysL[]`:

| Feld     | Beispiel                                                    |
|----------|-------------------------------------------------------------|
| `sDaysI` | `"1. Jul bis 30. Sep 2026 Mo - Fr; nicht 10. bis 28. Aug 2026"` — Verkehrstage im Klartext |
| `sDaysR` | `"nicht täglich"`                                            |
| `sDaysB` | dieselbe Info als Bitmaske (Hex, ein Bit pro Betriebstag)    |

Das ist die einzige Methode, die **Verkehrstage** liefert — nützlich für
"fährt diese Fahrt auch in den Ferien?".

### JourneyCourse — Linienverlauf als Polyline

```json
{
  "meth": "JourneyCourse",
  "req": {"jid": "1|4809|1|1|16092026"}
}
```

**Response**: `res.common.polyL[0]` mit

- `crdEncYX`: **Google-Encoded-Polyline** (`delta: true`, `dim: 2`,
  `type: "WGS84"`, Faktor `1e5`) — dasselbe Format wie bei Google Maps,
  mit dem Standard-Algorithmus dekodierbar.
- `ppLocRefL`: `{locX, ppIdx}` — verknüpft Polyline-Punkte mit Halten aus
  `common.locL`.

Es gibt einen Punkt **pro Halt**, keine straßengenaue Geometrie. `JourneyDetails`
akzeptiert zusätzlich `getPolyline: true` und `getPasslist: true` und liefert
dieselbe Geometrie zusammen mit den Zwischenhalten.

### LineMatch / LineDetails — Linien

```json
{"meth": "LineMatch", "req": {"input": "18"}}
```

**Response** (`res.lineL[]`): `{lineId, prodX}`, das Produkt in
`common.prodL[]`. Weitere Felder wie `type: "S"` führen zu `HAMM`.

⚠️ Der Datenbestand geht **weit über die KVB hinaus**: `"1"` trifft auch
`de:aac:…` (Aachen), `de:vrr:…` (VRR) und `nl:ln:…` (Niederlande). Die
`lineId`-Präfixe unterscheiden: Köln/Bonn ist `de:vrs:`.

```json
{"meth": "LineDetails", "req": {"lineId": "de:vrs:18"}}
```

Nur die volle `lineId` funktioniert — `"18"` → `FAIL` ("line not found").
Zusätzliche Felder (`date`, `getStopL`, `getPolyline`) → `HAMM`.

**Response**: `res.common.prodL[0]` mit `prodCtx` (`catOut`: `"Str"`/`"Bus"`,
`line`, `lineId`), `oprX` → Betreiber in `common.opL`, und einem
`stat`-Block:

| Feld      | Bedeutung                                              | KVB-Wert (2026-09-16) |
|-----------|--------------------------------------------------------|------------------------|
| `cnt`     | Fahrten im Fahrplan                                    | 1062 (Linie 18)        |
| `cncl`    | Ausfälle                                               | 0                      |
| `ont`     | pünktliche Fahrten                                     | 0                      |
| `rt`      | Fahrten mit Echtzeitdaten                              | 0                      |
| `him`     | Fahrten mit Störungsmeldung                            | 0                      |
| `delGrpL` | Minuten-Grenzen des Verspätungs-Histogramms            | `[1,2,3,…,10,15,20,30]`|
| `delCntL` | Anzahl Fahrten pro Grenze                              | alles 0                |

Das Schema für eine **Pünktlichkeitsstatistik** ist also da, aber außer `cnt`
füllt die KVB offenbar nichts — schade, das wäre die einzige aggregierte
Qualitätskennzahl der API gewesen.

## Methoden-Inventar

Systematisch durchprobiert (leerer `req`, ~55 Kandidaten aus dem
HAFAS-Methodenkanon). Ein unbekannter Methodenname antwortet mit `HAMM` —
damit lässt sich sauber trennen, was existiert.

**Vorhanden und genutzt:** `LocMatch`, `LocGeoPos`, `LocDetails`,
`LocGeoReach`, `StationBoard`, `JourneyDetails`, `JourneyMatch`,
`JourneyGeoPos`, `JourneyCourse`, `TripSearch`, `Reconstruction`,
`SearchOnTrip`, `GisRoute`, `HimSearch`, `HimGeoPos`, `LineMatch`,
`LineDetails`, `ServerInfo`.

**Rezept zum Schema-Knacken:** Da `HAMM` nicht sagt, *welches* Feld schuld
ist, hilft nur das Gegenteil von „alles auf einmal“: pro Request **genau ein
Feld** schicken. `HAMM` = Feldname existiert nicht, jeder andere Fehler =
Feld akzeptiert, Dienst lief an. So fällt die gültige Feldliste in ~20
Requests raus, danach kombiniert man nur noch die Überlebenden. Auf diesem
Weg fielen `GisRoute` (`gisCtx`) und `SearchOnTrip` (`ctxRecon`).

**Vorhanden, Request-Schema noch nicht geknackt:**

| Methode      | Stand                                                                                      |
|--------------|---------------------------------------------------------------------------------------------|
| `JourneyTree` | Leerer `req` → `OK` mit leerem `jnyTreeNodeL`; jede Parameter-Variante → `HAMM`.            |
| `Subscr*`    | `SubscrCreate`, `SubscrSearch`, `SubscrDetails`, `SubscrUserCreate` antworten mit `ERROR` statt `HAMM` — existieren also, brauchen aber vermutlich einen registrierten Nutzer (Push-Abos). |

**Nicht vorhanden** (alle `HAMM`): `StationBoardTree`, `TimetableInfo`,
`PoiSearch`, `Themes`, `NearbySearch`, `Geometry`, `FareSearch`, `Ticket`,
`PriceSearch`, `BestPrice`, `LocData`, `GisLocation`, `MatchSvc`,
`Departure`, `Arrival`, `Kaleidoscope`, `SubscrChannelList`, `AttrSearch`,
`OperatorSearch`, `ProductSearch`, `CalendarSearch`, `CheckIn`.

Alles Tarif-/Preisbezogene fehlt also komplett — die API kennt zwar
`ovwTrfRefL`-Referenzen in Verbindungen, aber keine Methode, die daraus
Preise macht.

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

| Code    | Bedeutung                                                                                  |
|---------|--------------------------------------------------------------------------------------------|
| `OK`    | Erfolg                                                                                     |
| `H9360` | Datum außerhalb der gültigen Fahrplanperiode                                               |
| `PARSE` | Fehlerhafter Request-Body (Top-Level `err`, nicht in `svcResL`)                            |
| `HAMM`  | Unbekannte Methode **oder** unbekanntes/falsch typisiertes Feld im `req` — nützlich zur Methoden-Erkennung, siehe [Methoden-Inventar](#methoden-inventar) |
| `NULLPTR`, `PARAMETER`, `LOCATION`, `DATE_TIME`, `DEPARTURE` | Methode existiert, Pflichtparameter fehlt — im Umkehrschluss der Beweis, dass es sie gibt |
| `FAIL`  | Generischer Fehler (in Tests nur gemockt, nicht live gesehen)                              |

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
