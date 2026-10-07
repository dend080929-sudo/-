import os, json, base64, time, secrets, urllib.parse, datetime, sys, io, argparse
import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes, serialization

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import builtins as _bi
try:
    _DBGP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tsum_debug.log")
    _DBG_LOG = open(_DBGP, "w" if (os.path.exists(_DBGP) and os.path.getsize(_DBGP) > 5_000_000) else "a",
                    encoding="utf-8", errors="replace")
except Exception:
    _DBG_LOG = None
def print(*a, **k):
    if _DBG_LOG is None:
        return
    k["file"] = _DBG_LOG; k["flush"] = True
    _bi.print(*a, **k)

BASE = "https://lgtmtm-game.linegame.jp/"
import tsum_login as _L
APPVER, RESVER, MSTVER = _L.APPVER, _L.RESVER, _L.MSTVER

def _sync_versions():
    """tsum_login 側で appver/resver/mstver が更新されたら取り込む。
    import 時のスナップショットのまま古いバージョンを送り続けるのを防ぐ。"""
    global APPVER, RESVER, MSTVER
    APPVER, RESVER, MSTVER = _L.APPVER, _L.RESVER, _L.MSTVER
    return APPVER, RESVER, MSTVER


def is_version_reject(resp):
    """retcode 201 (=「最新のバージョンにアップデートしてください」) かどうか。"""
    if not isinstance(resp, dict):
        return False
    if resp.get("retcode") != 201:
        return False
    msg = " ".join(str(resp.get(k, "")) for k in ("retmsg", "message", "errmsg", "error"))
    low = msg.lower()
    if any(k in msg for k in ("アップデート", "バージョン", "更新")):
        return True
    if any(k in low for k in ("version", "appver", "resver", "mstver", "update")):
        return True
    return not msg.strip()


def info_has_heart_data(info):
    """getInfo が本当に成功して userinfo を返したときだけ True。
    エラー応答(retcode!=0 / userinfo なし)を『ハート0』と誤読しないためのガード。"""
    if not isinstance(info, dict):
        return False
    rc = info.get("retcode")
    if rc is None or str(rc) != "0":
        return False
    ui = info.get("userinfo")
    return isinstance(ui, dict) and ("bheart" in ui or "pheart" in ui)


def info_has_coin_data(info):
    """コイン残量を信用してよい応答かどうか (insufficient_coin の誤判定防止)。"""
    if not isinstance(info, dict):
        return False
    rc = info.get("retcode")
    if rc is None or str(rc) != "0":
        return False
    ui = info.get("userinfo")
    return isinstance(ui, dict) and ("bcoin" in ui or "pcoin" in ui)


# バージョン復旧の連打防止 (失敗直後はしばらく再探索しない)
_VER_FIX = {"ts": 0.0, "marked": False}
_VER_FIX_COOLDOWN = 120


def _version_recovery_combos():
    """201 を食らったときに試す (appver, resver, mstver) の候補。現行の組み合わせは除外。"""
    base = (_L.APPVER, _L.RESVER, _L.MSTVER)
    try:
        new_av = _L.resolve_appver(force=True) or base[0]
    except Exception as e:
        print(f"[ver] appver再解決に失敗: {e}")
        new_av = base[0]
    avs = [new_av] + ([base[0]] if base[0] != new_av else [])
    combos = []
    for av in avs:
        for rv in _L.resver_candidates(av):
            for mv in (base[2], _L.MSTVER_FALLBACK):
                c = (av, rv, mv)
                if c != base and c not in combos:
                    combos.append(c)
    return combos[:8]


import tsum_login
TERMINAL = tsum_login.DEVICE_ID

USERID   = ""
HASH     = ""
CHECKVAL = ""

def growthyinfo():
    ts = datetime.datetime.now().strftime("%Y%m%d %H%M%S")
    return json.dumps({
        "sdkVersion":"3.0","osVer":"9","terminalId":TERMINAL,
        "deviceName":"SM-A805N","country":"JP","language":"ja",
        "networkType":2,"carrier":"440/00","clientTimestamp":ts
    }, separators=(",",":"))

def aes_gcm_encrypt(pt, key):
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(key).encrypt(nonce, pt.encode("utf-8"), None)
def aes_gcm_decrypt(data, key):
    return AESGCM(key).decrypt(data[:12], data[12:], None).decode("utf-8")
def rsa_oaep(key, pub_b64):
    pub = serialization.load_der_public_key(base64.b64decode(pub_b64))
    enc = pub.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                        algorithm=hashes.SHA256(), label=None))
    return base64.b64encode(enc).decode()

def qenc(v):
    return urllib.parse.quote(str(v), safe='')

def build_form(pairs):
    return "&".join(f"{k}={qenc(v)}" for k, v in pairs)

def _proxy_from_creds():
    """Forge が明示的な proxy 未指定(既定値)で作られたときに使う既定プロキシ。
    設定の実体は tsum_login.py の _creds_proxy() 1箇所にまとめてある。"""
    try:
        return _L._creds_proxy()
    except Exception:
        return None

_DEFAULT_PROXY = object()

def _proxy_from_value(proxy):
    if not proxy:
        return None
    if isinstance(proxy, dict):
        return {k: v for k, v in proxy.items() if v}
    return {"http": proxy, "https": proxy}

class Forge:
    _EV_CACHE = {"ts": 0.0, "cands": None}

    def __init__(self, userid=None, hashv=None, checkval=None, proxy=_DEFAULT_PROXY):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent":"tsumtsum/%s"%APPVER})
        _px = _proxy_from_creds() if proxy is _DEFAULT_PROXY else _proxy_from_value(proxy)
        if _px: self.s.proxies.update(_px)
        self.key = secrets.token_bytes(32)
        self.pub = None; self.kid = None; self.sid = None
        self.last_http_status = None
        self.last_error = None
        self.last_response = None
        self.last_gs_attempts = []
        self._last_multi_fail = None
        self.last_gs_response = None
        self._gs_combo = None
        self._last_info = None
        self.userid = userid or USERID
        self.hash = hashv or HASH
        self.checkval = checkval or CHECKVAL

    def get_public_key(self, tries=3):
        _sync_versions()
        u = BASE + "api/getPublicKey.nhn?appver=" + _L.APPVER
        self.s.headers.update({"User-Agent": "tsumtsum/%s" % _L.APPVER})
        last = None
        for i in range(tries):
            try:
                r = self.s.get(u, timeout=10)
                if r.status_code != 200:
                    raise requests.exceptions.RequestException(f"HTTP {r.status_code}")
                j = r.json()
                if not (j.get("public_key") or j.get("publicKey")) and j.get("retcode") == 201:
                    _L.resolve_appver(force=True)
                    _sync_versions()
                    u = BASE + "api/getPublicKey.nhn?appver=" + _L.APPVER
                    self.s.headers.update({"User-Agent": "tsumtsum/%s" % _L.APPVER})
                    continue
                self.pub = j.get("public_key") or j.get("publicKey")
                self.kid = str(j.get("kid",""))
                print(f"[getPublicKey] {r.status_code} kid={self.kid}")
                return self.pub
            except (requests.exceptions.RequestException, ValueError) as e:
                last = e
                print(f"  [getPublicKey 一時失敗 {i+1}/{tries}: {str(e)[:120]}]")
                if i < tries - 1:
                    time.sleep(0.5 * (i + 1))
        raise requests.exceptions.RequestException(f"getPublicKey failed: {last}")

    def _headers(self):
        h = {"Content-Type":"application/tsum"}
        if self.sid:
            h["X-Session-Id"] = self.sid
        else:
            h["X-Encrypted-Session-Key"] = rsa_oaep(self.key, self.pub)
            h["X-Key-Id"] = self.kid
        return h

    def _reset_session(self):
        """appver を変えたら公開鍵/セッションも取り直す。"""
        self.pub = None
        self.kid = None
        self.sid = None
        self.s.headers.update({"User-Agent": "tsumtsum/%s" % _L.APPVER})

    def post(self, path, specific_pairs):
        resp = self._post_once(path, specific_pairs)
        if not is_version_reject(resp) or getattr(self, "_in_ver_recovery", False):
            return resp

        now = time.time()
        if now - _VER_FIX["ts"] < _VER_FIX_COOLDOWN:
            return resp
        _VER_FIX["ts"] = now

        base = (_L.APPVER, _L.RESVER, _L.MSTVER)
        fixed = False
        self._in_ver_recovery = True
        try:
            combos = _version_recovery_combos()
            print(f"[ver] {path} が retcode201 で拒否 → 候補 {len(combos)} 件を試行 (現行={base})")
            for av, rv, mv in combos:
                _L.apply_versions(av, rv, mv, cache=False)
                _sync_versions()
                self._reset_session()
                try:
                    retry = self._post_once(path, specific_pairs)
                except Exception as e:
                    print(f"[ver] 候補 appver={av} resver={rv} mstver={mv} で例外: {e}")
                    continue
                if isinstance(retry, dict) and not is_version_reject(retry):
                    _L.apply_versions(av, rv, mv, cache=True)
                    _sync_versions()
                    _VER_FIX["ts"] = 0.0
                    _VER_FIX["marked"] = False
                    fixed = True
                    print(f"[ver] 復旧しました: appver={av} resver={rv} mstver={mv}")
                    return retry
            print(f"[ver] 全候補が拒否されました。元のバージョン {base} に戻します")
        except Exception as e:
            print(f"[ver] 復旧処理で例外: {e}")
        finally:
            if not fixed:
                # 復旧できなかった場合だけ元のバージョンへ戻す
                _L.apply_versions(*base, cache=False)
                _sync_versions()
                self._reset_session()
            self._in_ver_recovery = False
        return resp

    def _post_once(self, path, specific_pairs):
        _sync_versions()
        if not self.pub: self.get_public_key()
        common = [
            ("userid", self.userid),("appver",_L.APPVER),("resver",_L.RESVER),("mstver",_L.MSTVER),
            ("hash",self.hash),("os","2"),("lang","ja"),("checkval",self.checkval),
            ("countrycode","JP"),("growthyinfo", growthyinfo()),
        ]
        pairs = [("requestid", str(secrets.randbelow(4_000_000_000)))] + specific_pairs + common
        body = build_form(pairs)
        if not getattr(self, "quiet", False): print(f"\n[POST] {path}\n  plain={body[:240]}...")
        self.last_error = None; self.last_http_status = None; self.last_response = None
        r = None
        for _attempt in range(3):
            try:
                if not self.pub: self.get_public_key()
                enc = aes_gcm_encrypt(body, self.key)
                r = self.s.post(BASE+path, data=enc, headers=self._headers(), timeout=20)
                break
            except requests.exceptions.RequestException as e:
                if isinstance(e, requests.exceptions.ProxyError):
                    self.last_error = "proxy_error"
                elif isinstance(e, requests.exceptions.Timeout):
                    self.last_error = "timeout"
                else:
                    self.last_error = "network_error"
                if not getattr(self, "quiet", False):
                    print(f"  [{self.last_error} {_attempt+1}/3:{str(e)[:100]}]")
                if _attempt < 2:
                    time.sleep(0.5 * (_attempt + 1))
        if r is None:
            print(f"  [{self.last_error or 'network_error'}] {path} を3回試して失敗")
            return None
        self.last_http_status = r.status_code
        if not getattr(self, "quiet", False): print(f"  -> HTTP {r.status_code} ({len(r.content)}B)")
        sid = r.headers.get("X-Session-Id")
        if sid: self.sid = sid
        try:
            dec = aes_gcm_decrypt(r.content, self.key)
            if not getattr(self, "quiet", False): print(f"  resp(decrypted)= {dec[:500]}")
            try:
                parsed = json.loads(dec)
            except Exception:
                parsed = dec
            self.last_response = parsed
            if isinstance(parsed, dict) and parsed.get("retcode") == 0 and not _VER_FIX.get("marked"):
                # 実際に通った組み合わせを1プロセスに1度だけ記録しておく
                _VER_FIX["marked"] = True
                _L.mark_versions_good(_L.APPVER, _L.RESVER, _L.MSTVER)
            return parsed
        except Exception as e:
            print(f"  [復号不可:{e}] raw={r.content[:120]}")
            self.last_error = "decrypt_failed"
            return None

    def login(self, user_token, rankdt=0, trigger=0):
        _sync_versions()
        if not self.pub: self.get_public_key()
        common = [
            ("userid",""),("appver",_L.APPVER),("resver",_L.RESVER),("mstver",_L.MSTVER),
            ("hash",HASH),("os","2"),("lang","ja"),("checkval",""),
            ("countrycode","JP"),("growthyinfo",growthyinfo()),
        ]
        pairs = [("rankdt",rankdt),("accesstoken",user_token),("trigger",trigger)] + common
        body = build_form(pairs)
        print(f"\n[POST] login.nhn\n  plain={body[:160]}...")
        enc = aes_gcm_encrypt(body, self.key)
        r = self.s.post(BASE+"login.nhn", data=enc, headers=self._headers(), timeout=20)
        print(f"  -> HTTP {r.status_code} ({len(r.content)}B)")
        sid=r.headers.get("X-Session-Id")
        if sid: self.sid=sid
        try:
            dec = aes_gcm_decrypt(r.content, self.key)
            print(f"  resp= {dec[:400]}")
            return json.loads(dec)
        except Exception as e:
            print(f"  [復号不可:{e}]"); return None

    def eventid_for_shard(self):
        return "9999" if str(self.userid)[:3] in ("128", "139") else "0"

    def game_start(self, tsumid=860, hearttype=0, probmstver="2", eventid=None):
        if eventid is None:
            eventid = self.eventid_for_shard()
        return self.post("gameStart.nhn", [
            ("tsumid",tsumid),("eventid",eventid),("plazaeventid","0"),
            ("hearttype",hearttype),("probmstver",probmstver),("bonusflg","0"),
        ])

    def get_mast(self):
        return self.post("getMast.nhn", [("kind", "3|7|11|14|")])

    def event_candidates(self, mast=None):
        import time as _t
        if mast is None:
            try: mast = self.get_mast()
            except Exception: mast = None
        now = int(_t.time())
        rows = []
        if isinstance(mast, dict):
            for e in (mast.get("eventmst") or []):
                if not isinstance(e, dict): continue
                try:
                    sd = int(e.get("startdt") or 0); ed = int(e.get("enddt") or 0)
                    if sd <= now <= ed:
                        rows.append((int(e.get("pos") or 99), str(e.get("eventid"))))
                except Exception:
                    continue
        rows.sort()
        cand = [eid for _, eid in rows]
        for x in ("9999", "0"):
            if x not in cand:
                cand.append(x)
        return cand

    def game_end(self, playcode, score, coin=61, medal=5, exp=120, vanishcnt=None):
        otherresult = json.dumps({"playlog":{
            "coin":{"inval":[],"cnt":{"1":coin},"key":[1],"sum":str(coin),"req":"ne"},
            "medal":{"inval":[],"cnt":{"1":medal},"ani":{"1":medal},"key":[1],
                     "limit":{"time":"65.042282","ulpm":"70","blem":"0"},"sum":str(medal),"req":"ne"}
        }}, separators=(",",":"))
        return self.post("gameEnd.nhn", [
            ("score",score),("coin",coin),("medal",medal),("exp",exp),
            ("combo","17"),("chain","8"),("treasurecnt","0"),("fever","1"),
            ("skillcnt","0"),("extendflg","0"),
            ("basescore",score),("basecoin",coin),("basemedal",medal),
            ("maxmedal","87"),("baseexp",exp),("playtime","65"),
            ("vanishcnt", vanishcnt or "860,0|9,0|12,0|309,0|4,0|"),
            ("playcode",playcode),
            ("bombcnt","0,0|1,0|2,0|3,0|4,0|5,0|6,0|"),
            ("cheat","0"),("sizecnt","0,89|1,1|2,0|"),
            ("eventpt",""),("boxcnt",""),("genericval",""),
            ("otherresult",otherresult),
        ])

    def get_info(self):
        return self.post("getInfo.nhn", [])

    def resources(self, info=None):
        data = info if isinstance(info, dict) else self.get_info()
        ui = (data.get("userinfo") if isinstance(data, dict) else None) or {}
        return {k: ui.get(k, 0) for k in ("bcoin","pcoin","bheart","pheart","bruby","pruby")}

    def gacha_compflg(self, gachaid, info=None):
        data = info if isinstance(info, dict) else self.get_info()
        for g in ((data.get("gachainfo") if isinstance(data, dict) else None) or []):
            if g.get("gachaid") == gachaid:
                return g.get("compflg")
        return None

    def gacha_multi(self, gachaid, paytype=1):
        return self.post("gachaResultMulti.nhn", [("gachaid", gachaid), ("paytype", paytype)])

    def gacha_single(self, gachaid, paytype=1):
        return self.post("gachaResult.nhn", [("gachaid", gachaid), ("paytype", paytype)])

    @staticmethod
    def _draw_state(resp, gachaid):
        cf = None
        if isinstance(resp, dict):
            for g in (resp.get("gachainfo") or []):
                if g.get("gachaid") == gachaid:
                    cf = g.get("compflg"); break
        bc = (resp.get("userinfo") or {}).get("bcoin") if isinstance(resp, dict) else None
        return cf, bc

    def complete_box(self, gachaid, min_coin=30_000_000, paytype=1, sleep_s=0.05, max_iter=2000, log=print):
        info = self.get_info()
        if not isinstance(info, dict):
            return {"status": "network_error", "draws": 0}
        if not info_has_coin_data(info):
            return {"status": "api_error", "draws": 0, "resp": info}
        if self.gacha_compflg(gachaid, info) == 1:
            return {"status": "already_complete", "gachaid": gachaid}
        coin = self.resources(info)["bcoin"]
        if coin < min_coin:
            return {"status": "insufficient_coin", "have": coin, "need": min_coin}
        drawn = 0
        single = False
        self.quiet = True
        # max_iter は「ループ回数」の上限。単発モードでは 1ループ=1連 なので、
        # 固定値だとコインが残っていても途中で打ち切られてしまう
        # (例: 2億コイン=6,666連引けるのに 2,000 で止まって max_reached)。
        # 所持コインから引ける回数を見積もって上限を決める。
        # 打ち切りの役目は coin_ran_out が担うので、ここは暴走防止の保険でよい。
        need_iter = coin // 30_000 + 100          # 単発で引ける回数 + 余裕
        max_iter = max(max_iter, min(need_iter, 100_000))
        if log:
            log(f"  完売処理を開始 (所持コイン={coin:,} 想定最大={max_iter:,}ループ)")
        for _ in range(max_iter):
            if coin < (30_000 if single else 300_000):
                return {"status": "coin_ran_out", "draws": drawn, "coin": coin}
            if not single:
                r = self.gacha_multi(gachaid, paytype)
                if r is None:
                    return {"status": "network_error", "draws": drawn}
                if isinstance(r, dict) and r.get("retcode") == 0:
                    drawn += 10
                else:
                    if is_version_reject(r):
                        return {"status": "api_error", "draws": drawn, "resp": r}
                    # 「残り10体未満」以外（コイン不足・一時エラー等）もここに来るので、
                    # 応答を残しておき、単発でも駄目だったときの診断に使う。
                    self._last_multi_fail = r
                    single = True
                    if log: log(f"  10連が失敗 → 単発に切替（{drawn}連時点） resp={str(r)[:120]}")
                    continue
            else:
                r = self.gacha_single(gachaid, paytype)
                if r is None:
                    return {"status": "network_error", "draws": drawn}
                if isinstance(r, dict) and r.get("retcode") == 0:
                    drawn += 1
                else:
                    if self.gacha_compflg(gachaid) == 1:
                        return {"status": "complete", "draws": drawn}
                    return {"status": "draw_error", "draws": drawn, "resp": str(r)[:200],
                            "multi_resp": str(getattr(self, "_last_multi_fail", None))[:200]}
            cf, bc = self._draw_state(r, gachaid)
            if bc is not None:
                coin = bc
            if cf == 1:
                return {"status": "complete", "draws": drawn}
            if cf is None:
                info = self.get_info()
                if not isinstance(info, dict):
                    return {"status": "network_error", "draws": drawn}
                if self.gacha_compflg(gachaid, info) == 1:
                    return {"status": "complete", "draws": drawn}
                coin = self.resources(info)["bcoin"]
            if log and drawn % 200 == 0:
                log(f"  ...{drawn}引 (bcoin={coin})")
            if sleep_s:
                time.sleep(sleep_s)
        return {"status": "max_reached", "draws": drawn}

    def fill_coins(self, cap=200_000_000):
        info = self.get_info()
        cur = (info.get("userinfo") or {}).get("bcoin") or 0
        if cur >= cap:
            return cur
        add = cap - cur
        sett = [t.get("tsumid") for t in (info.get("tsuminfo") or []) if isinstance(t, dict) and t.get("setflg") in (1, "1")]
        tsums = [t.get("tsumid") for t in (info.get("tsuminfo") or []) if isinstance(t, dict)]
        try:
            evs = self.event_candidates() or ["0", "9999"]
        except Exception:
            evs = ["0", "9999"]
        for tid in ((sett + tsums)[:5] or [1]):
            for ht in (0, 1):
                for ev in evs:
                    r1 = self.game_start(tsumid=tid, hearttype=ht, probmstver="2", eventid=ev)
                    pc = (r1.get("userinfo") or {}).get("playcode") if isinstance(r1, dict) else None
                    if pc:
                        r2 = self.game_end(pc, score=95000, coin=add, medal=5, exp=120,
                                           vanishcnt=f"{tid},0|9,0|12,0|309,0|4,0|")
                        return (r2.get("userinfo") or {}).get("bcoin", cur) if isinstance(r2, dict) else cur
        return cur

    def release_tsum_lv(self, tsumid):
        return self.post("releaseTsumLv.nhn", [("tsumid", tsumid)])

    def cached_event_candidates(self, ttl=900):
        """開催中イベントIDの候補。getMast は重いのでプロセス内でキャッシュする。"""
        now = time.time()
        c = Forge._EV_CACHE
        if c["cands"] and (now - c["ts"] < ttl):
            return c["cands"]
        try:
            cands = self.event_candidates()
        except Exception as e:
            print(f"[gs] event_candidates失敗→fallback: {e}")
            cands = None
        if cands:
            c["cands"] = cands
            c["ts"] = now
        return cands or ["9999", "0"]

    def _hearttype_candidates(self, info=None):
        r = self.resources(info) if isinstance(info, dict) else {}
        hts = []
        if (r.get("bheart") or 0) > 0:
            hts.append(0)
        if (r.get("pheart") or 0) > 0:
            hts.append(1)
        for v in (0, 1):
            if v not in hts:
                hts.append(v)
        return hts

    def game_start_any(self, tsumid, info=None, max_try=12):
        """eventid / hearttype を総当たりして playcode を取る。
        決め打ちだと retcode 8(パラメータエラー) や 23(ハート残数不一致) で即失敗するため。
        成功した組み合わせは覚えて、次回から最初に試す。"""
        if info is None:
            info = getattr(self, "_last_info", None)
        evs = list(self.cached_event_candidates())
        base_ev = self.eventid_for_shard()
        if base_ev not in evs:
            evs.insert(0, base_ev)

        combos = []
        won = getattr(self, "_gs_combo", None)
        if won:
            combos.append(won)
        for ht in self._hearttype_candidates(info):
            for ev in evs:
                if (ev, ht) not in combos:
                    combos.append((ev, ht))

        attempts = []
        last = None
        for ev, ht in combos[:max_try]:
            r1 = self.game_start(tsumid=tsumid, hearttype=ht, eventid=ev)
            last = r1
            rc = (r1.get("retcode"), r1.get("retsubcode")) if isinstance(r1, dict) else None
            attempts.append((ev, ht, rc))
            pc = (r1.get("userinfo") or {}).get("playcode") or r1.get("playcode") if isinstance(r1, dict) else None
            if pc:
                self._gs_combo = (ev, ht)
                self.last_gs_attempts = attempts
                return pc, r1
            if isinstance(r1, dict) and r1.get("retcode") == 23:
                # 「ハート残数が一致しません」→ 手元のハート状態が古い。取り直して次の候補へ。
                fresh = self.get_info()
                if info_has_heart_data(fresh):
                    self._last_info = fresh
                    info = fresh
            time.sleep(0.15)
        self._gs_combo = None
        self.last_gs_attempts = attempts
        return None, last

    def level_tsum_once(self, tsumid, vanish=200000, score=10_000_000, info=None):
        pc, r1 = self.game_start_any(tsumid, info=info)
        if not pc:
            self.last_gs_response = r1
            return None
        return self.game_end(pc, score, vanishcnt=f"{tsumid},{vanish}|")

    def _tsum_lv(self, resp, tsumid):
        for t in (resp.get("tsuminfo") or []) if isinstance(resp, dict) else []:
            if t.get("tsumid") == tsumid:
                return t.get("lv"), t.get("limitlv")
        return None, None

    def hearts(self, info=None):
        r = self.resources(info)
        return (r.get("bheart") or 0) + (r.get("pheart") or 0)

    def max_tsum_level(self, tsumid, target=50, min_heart=10, sleep_s=0.6, max_iter=40, log=print):
        info0 = self.get_info()
        if not isinstance(info0, dict):
            return {"status": "network_error"}
        if not info_has_heart_data(info0):
            # getInfo がエラー応答(retcode!=0 / userinfo なし)。ハート0と誤読しない。
            return {"status": "api_error", "resp": info0}
        h0 = self.hearts(info0)
        if h0 < min_heart:
            return {"status": "insufficient_heart", "have": h0, "need": min_heart}
        self._last_info = info0
        last_lv = None
        for _ in range(max_iter):
            r = self.level_tsum_once(tsumid)
            if not isinstance(r, dict) or r.get("retcode") != 0:
                _now = self.get_info()
                if info_has_heart_data(_now):
                    self._last_info = _now
                    if self.hearts(_now) < 1:
                        return {"status": "heart_ran_out", "lv": last_lv}
                time.sleep(1.5)
                r = self.level_tsum_once(tsumid)
                if not isinstance(r, dict) or r.get("retcode") != 0:
                    return {"status": "level_error", "lv": last_lv,
                            "resp": str(r if r is not None else self.last_gs_response)[:200],
                            "attempts": list(self.last_gs_attempts)}
            lv, _cap = self._tsum_lv(r, tsumid)
            if log: log(f"  tsum{tsumid}: lv={lv}")
            if lv is not None and lv >= target:
                return {"status": "done", "lv": lv}
            ru = self.release_tsum_lv(tsumid)
            if isinstance(ru, dict):
                ti = ru.get("tsuminfo") or {}
                cap = ti.get("limitlv")
                if ru.get("retcode") not in (0, None):
                    return {"status": "release_error", "lv": lv, "resp": str(ru)[:200]}
            if lv == last_lv:
                return {"status": "stuck", "lv": lv, "note": "上限開放できず(コイン不足?)"}
            last_lv = lv
            time.sleep(sleep_s)
        return {"status": "max_iter", "lv": last_lv}


def load_session(args):
    if args.userid and args.hash and args.checkval:
        return args.userid, args.hash, args.checkval

    if not getattr(args, "no_headless", False):
        try:
            import tsum_login
            print("[session] 通信ログイン(ヘッドレス)を試行中...")
            s = tsum_login.headless_session()
            if s and s.get("userid") and s.get("hash") and s.get("checkval"):
                print(f"[session] ★通信ログイン成功 userid={s['userid']} checkval={s['checkval']}")
                return (args.userid or s["userid"], args.hash or s["hash"], args.checkval or s["checkval"])
            print("[session] 通信ログイン不可(トークン失効?)→ エミュメモリ検出にフォールバック")
        except Exception as e:
            print(f"[session] 通信ログイン失敗: {e} → エミュメモリ検出にフォールバック")

    try:
        import tsum_session
        print("[session] ゲームメモリから自動検出中...")
        s = tsum_session.detect()
        print(f"[session] userid={s['userid']} checkval={s['checkval']} hash={s['hash'][:16]}...")
        if not all(s.values()):
            print("[session] 一部未検出。ゲームをホーム(ログイン後)まで進めてください。")
        return (args.userid or s["userid"], args.hash or s["hash"], args.checkval or s["checkval"])
    except Exception as e:
        print(f"[session] 自動検出失敗: {e}")
        return args.userid or USERID, args.hash or HASH, args.checkval or CHECKVAL


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="ツムツム スコア/コイン/EXP 偽装 (session自動検出)")
    ap.add_argument("--score", type=int, default=None, help="記録するスコア(basescore)")
    ap.add_argument("--coin",  type=int, default=None, help="獲得コイン(basecoin・残高に加算)")
    ap.add_argument("--exp",   type=int, default=None, help="獲得EXP(baseexp・レベルに加算)")
    ap.add_argument("--medal", type=int, default=5,    help="獲得メダル(basemedal)")
    ap.add_argument("--tsumid",type=int, default=860,  help="使用ツムID")
    ap.add_argument("--userid", default=None, help="手動指定(省略時 自動検出)")
    ap.add_argument("--hash",   default=None, help="手動指定(省略時 自動検出)")
    ap.add_argument("--checkval", default=None, help="手動指定(省略時 自動検出)")
    ap.add_argument("--no-headless", action="store_true", help="通信ログインを使わずエミュメモリ検出にする")
    ap.add_argument("score_pos", nargs="?", type=int, default=None, help=argparse.SUPPRESS)
    args = ap.parse_args()

    score = args.score if args.score is not None else args.score_pos
    if score is None and args.coin is None and args.exp is None:
        score = 1000000

    uid, hsh, ckv = load_session(args)
    if not (uid and hsh and ckv):
        print("[中断] session定数が揃いません。ゲームにログインしてホームまで進めてください。")
        raise SystemExit(1)

    f = Forge(userid=uid, hashv=hsh, checkval=ckv)
    f.get_public_key()

    print("\n=== gameStart.nhn ===")
    r1 = f.game_start(tsumid=args.tsumid)
    playcode = None
    if isinstance(r1, dict):
        print("  retcode=", r1.get("retcode"))
        playcode = (r1.get("userinfo") or {}).get("playcode") or r1.get("playcode")
        bcoin = (r1.get("userinfo") or {}).get("bcoin")
        print(f"  playcode={playcode}  現在bcoin={bcoin}")
    if not playcode:
        print("[中断] playcode取得失敗(checkval失効? ゲーム再ログイン→再検出を)。")
        raise SystemExit(1)

    print("\n=== gameEnd.nhn (偽装送信) ===")
    r2 = f.game_end(playcode,
                    score=score if score is not None else 95000,
                    coin=args.coin if args.coin is not None else 61,
                    medal=args.medal,
                    exp=args.exp if args.exp is not None else 120)
    if isinstance(r2, dict):
        print("  retcode=", r2.get("retcode"), "retmsg=", r2.get("retmsg"))
        ui = r2.get("userinfo") or {}
        print(f"  → 反映後: bcoin={ui.get('bcoin')} lv={ui.get('lv')} bmedal={ui.get('bmedal')} earnedexp={ui.get('earnedexp')}")
        rl = (r2.get("rankinginfo") or {}).get("rankinglist") or []
        for e in rl:
            if e.get("selfflg") == 1:
                print(f"  ★自分の記録: name={e.get('name')} score={e.get('score')} order={e.get('order')}")
        open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "gameend_response.json"),"w",encoding="utf-8").write(
            json.dumps(r2, ensure_ascii=False, indent=2))
