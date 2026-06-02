# BIS-63 target banks — coverage checklist

Generated from `cb_corpus/banks.py`. Use the **Adapter** column to track buildout: `generic` = speeches (C1) + papers (D) only; a named class = bespoke native listings (A/B/E/F).

| # | code | bank | country | domain | verify | adapter |
|--:|------|------|---------|--------|:------:|---------|
| 1 | `ae` | Central Bank of the United Arab Emirates | United Arab Emirates | centralbank.ae | ⚠️ | generic |
| 2 | `ar` | Central Bank of Argentina | Argentina | bcra.gob.ar |  | generic |
| 3 | `at` | Oesterreichische Nationalbank | Austria | oenb.at |  | generic |
| 4 | `au` | Reserve Bank of Australia | Australia | rba.gov.au |  | generic |
| 5 | `ba` | Central Bank of Bosnia and Herzegovina | Bosnia and Herzegovina | cbbh.ba | ⚠️ | generic |
| 6 | `be` | National Bank of Belgium | Belgium | nbb.be |  | generic |
| 7 | `bg` | Bulgarian National Bank | Bulgaria | bnb.bg |  | generic |
| 8 | `br` | Central Bank of Brazil | Brazil | bcb.gov.br |  | generic |
| 9 | `ca` | Bank of Canada | Canada | bankofcanada.ca |  | generic |
| 10 | `ch` | Swiss National Bank | Switzerland | snb.ch |  | generic |
| 11 | `cl` | Central Bank of Chile | Chile | bcentral.cl |  | generic |
| 12 | `cn` | People's Bank of China | China | pbc.gov.cn |  | generic |
| 13 | `co` | Central Bank of Colombia | Colombia | banrep.gov.co |  | generic |
| 14 | `cz` | Czech National Bank | Czechia | cnb.cz |  | generic |
| 15 | `de` | Deutsche Bundesbank | Germany | bundesbank.de |  | generic |
| 16 | `dk` | Danmarks Nationalbank | Denmark | nationalbanken.dk |  | generic |
| 17 | `dz` | Bank of Algeria | Algeria | bank-of-algeria.dz | ⚠️ | generic |
| 18 | `ecb` | European Central Bank | Euro area | ecb.europa.eu |  | `ECBAdapter` |
| 19 | `ee` | Eesti Pank | Estonia | eestipank.ee |  | generic |
| 20 | `es` | Banco de Espana | Spain | bde.es |  | generic |
| 21 | `fi` | Bank of Finland | Finland | suomenpankki.fi |  | generic |
| 22 | `fr` | Bank of France | France | banque-france.fr |  | generic |
| 23 | `gb` | Bank of England | United Kingdom | bankofengland.co.uk |  | generic |
| 24 | `gr` | Bank of Greece | Greece | bankofgreece.gr |  | generic |
| 25 | `hk` | Hong Kong Monetary Authority | Hong Kong SAR | hkma.gov.hk |  | generic |
| 26 | `hr` | Croatian National Bank | Croatia | hnb.hr |  | generic |
| 27 | `hu` | Magyar Nemzeti Bank | Hungary | mnb.hu |  | generic |
| 28 | `id` | Bank Indonesia | Indonesia | bi.go.id |  | generic |
| 29 | `ie` | Central Bank of Ireland | Ireland | centralbank.ie |  | generic |
| 30 | `il` | Bank of Israel | Israel | boi.org.il |  | generic |
| 31 | `in` | Reserve Bank of India | India | rbi.org.in |  | generic |
| 32 | `is` | Central Bank of Iceland | Iceland | cb.is |  | generic |
| 33 | `it` | Bank of Italy | Italy | bancaditalia.it |  | generic |
| 34 | `jp` | Bank of Japan | Japan | boj.or.jp |  | generic |
| 35 | `kr` | Bank of Korea | Korea | bok.or.kr |  | generic |
| 36 | `kw` | Central Bank of Kuwait | Kuwait | cbk.gov.kw | ⚠️ | generic |
| 37 | `lt` | Lietuvos bankas | Lithuania | lb.lt |  | generic |
| 38 | `lu` | Banque centrale du Luxembourg | Luxembourg | bcl.lu |  | generic |
| 39 | `lv` | Latvijas Banka | Latvia | bank.lv |  | generic |
| 40 | `ma` | Bank Al-Maghrib | Morocco | bkam.ma | ⚠️ | generic |
| 41 | `mk` | National Bank of the Republic of North Macedonia | North Macedonia | nbrm.mk | ⚠️ | generic |
| 42 | `mx` | Bank of Mexico | Mexico | banxico.org.mx |  | generic |
| 43 | `my` | Bank Negara Malaysia | Malaysia | bnm.gov.my |  | generic |
| 44 | `nl` | De Nederlandsche Bank | Netherlands | dnb.nl |  | generic |
| 45 | `no` | Norges Bank | Norway | norges-bank.no |  | generic |
| 46 | `nz` | Reserve Bank of New Zealand | New Zealand | rbnz.govt.nz |  | generic |
| 47 | `pe` | Central Reserve Bank of Peru | Peru | bcrp.gob.pe |  | generic |
| 48 | `ph` | Bangko Sentral ng Pilipinas | Philippines | bsp.gov.ph |  | generic |
| 49 | `pl` | Narodowy Bank Polski | Poland | nbp.pl |  | generic |
| 50 | `pt` | Banco de Portugal | Portugal | bportugal.pt |  | generic |
| 51 | `ro` | National Bank of Romania | Romania | bnr.ro |  | generic |
| 52 | `rs` | National Bank of Serbia | Serbia | nbs.rs |  | generic |
| 53 | `ru` | Central Bank of the Russian Federation | Russia | cbr.ru |  | generic |
| 54 | `sa` | Saudi Central Bank | Saudi Arabia | sama.gov.sa |  | generic |
| 55 | `se` | Sveriges Riksbank | Sweden | riksbank.se |  | generic |
| 56 | `sg` | Monetary Authority of Singapore | Singapore | mas.gov.sg |  | generic |
| 57 | `si` | Banka Slovenije | Slovenia | bsi.si |  | generic |
| 58 | `sk` | Narodna banka Slovenska | Slovakia | nbs.sk |  | generic |
| 59 | `th` | Bank of Thailand | Thailand | bot.or.th |  | generic |
| 60 | `tr` | Central Bank of the Republic of Turkiye | Turkiye | tcmb.gov.tr |  | generic |
| 61 | `us` | Federal Reserve System | United States | federalreserve.gov |  | `FedAdapter` |
| 62 | `vn` | State Bank of Vietnam | Vietnam | sbv.gov.vn | ⚠️ | generic |
| 63 | `za` | South African Reserve Bank | South Africa | resbank.co.za |  | generic |

- **Total banks:** 63
- **Bespoke adapters:** 2 (ecb, us) — the other 61 run on the generic adapter.
- **Domains to verify on first run (⚠️):** dz, ba, kw, ma, mk, ae, vn

## Suggested adapter build order (highest A3/E volume first)
`gb` BoE · `jp` BoJ · `ca` BoC · `au` RBA · `in` RBI · `ch` SNB · `se` Riksbank · `no` Norges Bank · `de` Bundesbank · `it` Banca d'Italia
