#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, json, urllib.request

DEFAULT = [("BTC","bitcoin"),("ETH","ethereum"),("SOL","solana"),
("XRP","ripple"),("DOGE","dogecoin"),("BNB","binancecoin"),
("TON","the-open-network"),("ADA","cardano"),("AVAX","avalanche-2"),
("LINK","chainlink"),("TRX","tron"),("SUI","sui"),("USDT","tether")]

TBL={"btc":"bitcoin","eth":"ethereum","sol":"solana","ton":"the-open-network",
"trx":"tron","usdt":"tether","xrp":"ripple","doge":"dogecoin","ada":"cardano",
"bnb":"binancecoin","dot":"polkadot","avax":"avalanche-2","ltc":"litecoin",
"link":"chainlink","matic":"matic-network","uni":"uniswap","shib":"shiba-inu",
"near":"near","atom":"cosmos","fil":"filecoin","ape":"apecoin","sui":"sui",
"op":"optimism","arb":"arbitrum","inj":"injective","bonk":"bonk",
"pepe":"pepe","wld":"worldcoin","mkr":"maker","aave":"aave","stx":"stacks",
"ordi":"ordinals","runes":"the-runes","pyth":"pyth-network","jup":"jupiter"}

def fmt_usd(usd):
    if usd>=1000: return f"${usd:,.2f}"
    if usd>=1: return f"${usd:.2f}"
    if usd>=0.01: return f"${usd:.4f}"
    return f"${usd:.6f}"

def main():
    coins=DEFAULT
    if len(sys.argv)>1:
        raw=sys.argv[1].replace(" ","")
        names=[c for c in raw.split(",") if c]
        coins=[(n.upper(),TBL.get(n.lower(),n.lower())) for n in names]
    ids=",".join(c[1] for c in coins)
    url=("https://api.coingecko.com/api/v3/simple/price"
         f"?ids={ids}&vs_currencies=usd&include_24hr_change=true")
    rq=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
    try:
        with urllib.request.urlopen(rq,timeout=15) as r:
            found=json.loads(r.read().decode())
    except Exception as e:
        print("MKT-WATCH\nERR: "+str(e)); return
    rows=[]; na=[]
    for sym,cid in coins:
        if cid not in found:
            na.append(sym); continue
        d=found[cid]; usd=d.get("usd"); chg=d.get("usd_24h_change")
        if usd is None or usd==0:
            na.append(sym); continue
        ar="UP" if (chg or 0)>=0 else "DN"
        pct=f"{chg:+.2f}%" if chg is not None else "--"
        rows.append((usd, f"{sym} {fmt_usd(usd)} {pct} [{ar}]"))
    rows.sort(key=lambda t:t[0], reverse=True)
    print("MKT-WATCH\n"+"\n".join(line for _,line in rows))
    for s in na:
        print(s+": unavailable")

if __name__=="__main__":
    main()
