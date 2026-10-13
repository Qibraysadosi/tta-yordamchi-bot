"""TZ-26: GitHub'dan yangilash — soxta GitHub API bilan oqim, kunlik tekshiruv, Mini App va sozlamalar."""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx2
import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot import app as bot_app
from bot import scheduler
from bot.config import DEFAULT_GITHUB_REPO, ConfigError, load_settings
from bot.github import GitHubClient, GitHubError, Release, is_newer, parse_version
from bot.updater import CALLBACK_GITHUB_CHECK, CALLBACK_GITHUB_INSTALL, UpdateManager, fetch_release
from test_admin import release_zip
from test_flow import OWNER, Env, make_app_dir, run
from test_webapp import MEMBER, App

REPO = "Qibraysadosi/tta-yordamchi-bot"
TOKEN = "test_token_0123456789"  # soxta token (GitHub formatiga o'xshamaydi — maxfiy satr tekshiruvi uchun)
GUEST = 111
API = "https://api.github.com"
ASSET_URL = f"{API}/repos/{REPO}/releases/assets/1"


def release_json(version: str, *, asset: str | None = None, size: int = 1234, notes: str = "", tag: str | None = None) -> dict:
    assets = [] if asset is None else [{"name": asset, "url": ASSET_URL, "size": size, "content_type": "application/zip"}]
    tag = tag if tag is not None else f"v{version}"
    return {
        "tag_name": tag, "name": tag, "body": notes, "html_url": f"https://github.com/{REPO}/releases/tag/{tag}",
        "zipball_url": f"{API}/repos/{REPO}/zipball/{tag}", "assets": assets,
    }


class FakeGitHub:
    """Soxta GitHub REST API: release, aktiv (S3 ga yo'naltirish), tarmoq arxivi (codeload), VERSION fayli."""

    def __init__(self, *, private: bool = True, release: dict | None = None, asset: bytes = b"",
                 branch_version: str = "1.3.0", branch_zip: bytes = b""):
        self.private = private
        self.release = release
        self.asset = asset
        self.branch_version = branch_version
        self.branch_zip = branch_zip
        self.rate_limited = False
        self.down = False
        self.branch_exists = True  # False — GITHUB_BRANCH tarmog'i (yoki undagi VERSION) yo'q
        self.gate: asyncio.Event | None = None  # o'rnatilsa, aktiv yuklash shu hodisani kutadi
        self.api_gate: asyncio.Event | None = None  # o'rnatilsa, API so'rovlari shu hodisani kutadi
        self.requests: list[httpx2.Request] = []

    def _authorized(self, request: httpx2.Request) -> bool:
        return not self.private or request.headers.get("authorization") == f"Bearer {TOKEN}"

    async def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host in ("objects.githubusercontent.com", "codeload.github.com"):
            if "authorization" in request.headers:  # S3 ikki xil autentifikatsiyani rad etadi
                return httpx2.Response(400, text="Only one auth mechanism allowed")
            if self.gate is not None:
                await self.gate.wait()
            return httpx2.Response(200, content=self.asset if host.startswith("objects") else self.branch_zip)
        assert host == "api.github.com", host
        if self.api_gate is not None:
            await self.api_gate.wait()
        if self.down:
            return httpx2.Response(503, json={"message": "Service Unavailable"})
        if self.rate_limited:
            return httpx2.Response(403, headers={"x-ratelimit-remaining": "0"}, json={"message": "API rate limit exceeded"})
        if "authorization" in request.headers and not self._authorized(request):
            return httpx2.Response(401, json={"message": "Bad credentials"})
        if not self._authorized(request):
            return httpx2.Response(404, json={"message": "Not Found"})  # yopiq repo tokensiz ko'rinmaydi
        if path == f"/repos/{REPO}/releases/latest":
            if self.release is None:
                return httpx2.Response(404, json={"message": "Not Found"})
            return httpx2.Response(200, json=self.release)
        if path == f"/repos/{REPO}/releases/assets/1":
            assert request.headers["accept"] == "application/octet-stream"
            return httpx2.Response(302, headers={"location": "https://objects.githubusercontent.com/r/1/asset.zip?X-Amz-Signature=x"})
        if path == f"/repos/{REPO}/contents/VERSION":
            assert request.url.params["ref"] == "main"
            if not self.branch_exists:
                return httpx2.Response(404, json={"message": "No commit found for the ref main"})
            return httpx2.Response(200, content=f"{self.branch_version}\n".encode())
        if path == f"/repos/{REPO}":
            return httpx2.Response(200, json={"full_name": REPO, "private": self.private})
        if path.startswith(f"/repos/{REPO}/zipball/"):
            return httpx2.Response(302, headers={"location": f"https://codeload.github.com/{REPO}/legacy.zip/main"})
        return httpx2.Response(404, json={"message": "Not Found"})

    def client(self, token: str | None = TOKEN, **kwargs) -> GitHubClient:
        transport = httpx2.MockTransport(self.handler)
        return GitHubClient(REPO, token, "main", client=httpx2.AsyncClient(transport=transport, follow_redirects=True), **kwargs)

    def api_calls(self) -> list[str]:
        return [r.url.path for r in self.requests if r.url.host == "api.github.com"]


# ----------------------------------------------------------------------------- birlik


def test_version_helpers():
    assert parse_version("v1.6.0") == (1, 6, 0) and parse_version("release-2.0.1") == (2, 0, 1)
    assert parse_version("1.6.0\n") == (1, 6, 0) and parse_version("latest") is None and parse_version(None) is None
    assert is_newer("1.6.0", "1.5.0") and is_newer("1.10.0", "1.9.3") and is_newer("2.0.0", "1.99.99")
    assert not is_newer("1.5.0", "1.5.0") and not is_newer("1.4.9", "1.5.0") and not is_newer("abc", "1.0.0")
    assert is_newer("1.0.0", "?")  # joriy versiya tushunarsiz bo'lsa, raqamli nomzod yangi hisoblanadi
    with pytest.raises(ValueError):
        GitHubClient("noto'g'ri repo")


def test_client_release_asset_and_errors():
    async def go():
        gh = FakeGitHub(release=release_json("1.4.0", asset="claude-telegram-bot-v1.4.0.zip", notes="Izoh"), asset=b"PK-asset")
        client = gh.client()
        release = await client.latest()
        assert (release.version, release.source, release.tag, release.asset_name) == ("1.4.0", "release", "v1.4.0", "claude-telegram-bot-v1.4.0.zip")
        assert release.notes == "Izoh" and release.label == "GitHub Release v1.4.0 (claude-telegram-bot-v1.4.0.zip)"
        assert await client.download(release) == b"PK-asset"
        api_request = [r for r in gh.requests if r.url.path.endswith("/assets/1")][0]
        assert api_request.headers["authorization"] == f"Bearer {TOKEN}"
        s3_request = [r for r in gh.requests if r.url.host == "objects.githubusercontent.com"][0]
        assert "authorization" not in s3_request.headers  # token boshqa domenga ketmaydi

        # aktiv nomi boshqacha / bir nechta — mos keladigani tanlanadi
        gh.release = release_json("1.4.0", asset="boshqa.zip")
        gh.release["assets"].append({"name": "claude-telegram-bot-v1.4.0.zip", "url": ASSET_URL, "size": 5})
        assert (await client.latest()).asset_name == "claude-telegram-bot-v1.4.0.zip"
        # faqat begona .zip aktiv (masalan skrinshotlar) — bot arxivi emas, manba arxivi olinadi
        gh.release = release_json("1.4.0", asset="screens.zip")
        release = await client.latest()
        assert release.asset_name is None and release.download_url.endswith("/zipball/v1.4.0")
        # aktivsiz release — teg manba arxivi
        gh.release = release_json("1.4.0")
        release = await client.latest()
        assert release.asset_name is None and release.download_url.endswith("/zipball/v1.4.0") and "manba arxivi" in release.label
        # versiya tegda yo'q, aktiv nomida bor
        gh.release = release_json("1.4.0", asset="claude-telegram-bot-v1.4.0.zip", tag="latest")
        assert (await client.latest()).version == "1.4.0"
        gh.release = release_json("0.0.0", tag="nightly")
        with pytest.raises(GitHubError, match="versiya raqami yo'q"):
            await client.latest()

        # xatolar — foydalanuvchiga tushunarli
        gh.release = release_json("1.4.0", asset="claude-telegram-bot-v1.4.0.zip")
        with pytest.raises(GitHubError, match="GITHUB_TOKEN yozing"):
            await gh.client(token=None).latest()  # yopiq repo tokensiz
        with pytest.raises(GitHubError, match="noto'g'ri yoki muddati tugagan"):
            await gh.client(token="xato_token_0123456789").latest()
        gh.private = False
        assert (await gh.client(token=None).latest()).version == "1.4.0"  # ochiq repo tokensiz ishlaydi
        gh.private = True
        gh.rate_limited = True
        with pytest.raises(GitHubError, match="chegarasi"):
            await client.latest()
        gh.rate_limited = False
        gh.down = True
        with pytest.raises(GitHubError, match="vaqtincha"):
            await client.latest()
        gh.down = False
        too_big = Release("9.9.9", "release", "v9", "v9", "", ASSET_URL, "a.zip", 30 * 1024 * 1024, "")
        with pytest.raises(GitHubError, match="juda katta"):
            await client.download(too_big)
        small = gh.client(max_bytes=4)
        with pytest.raises(GitHubError, match="juda katta"):
            await small.download(await small.latest())
        # manba arxivi (zipball) hajmi oldindan noma'lum — chegarani oqim paytida ushlaydi
        gh.release, gh.branch_zip = None, b"x" * 16
        branch = await small.latest()
        assert branch.size == 0
        with pytest.raises(GitHubError, match="juda katta"):
            await small.download(branch)
        assert any(r.url.host == "codeload.github.com" for r in gh.requests)
        gh.release = release_json("1.4.0", asset="claude-telegram-bot-v1.4.0.zip")

        async def broken(request):
            raise httpx2.ConnectError("yo'q")

        offline = GitHubClient(REPO, TOKEN, client=httpx2.AsyncClient(transport=httpx2.MockTransport(broken)))
        with pytest.raises(GitHubError, match="ulanib bo'lmadi"):
            await offline.latest()
    run(go())


def test_branch_fallback_and_changed_files(tmp_path):
    """Release yo'q → main tarmog'i arxivi (VERSION orqali); tests/, docs/ e'tiborsiz; o'zgargan fayllar ro'yxati."""
    app = make_app_dir(tmp_path / "app")
    upd = UpdateManager(app_dir=app, data_dir=tmp_path / "data")
    branch_zip = release_zip(
        app, "1.3.0", marker="main", root="Qibraysadosi-tta-yordamchi-bot-0a1b2c3",
        extra={"tests/test_x.py": "x = 1\n", "docs/TZ.md": "# TZ\n", "scripts/build_release.py": "print(1)\n", "UPDATE_NOTES.md": "tarmoqdan"},
    )
    (app / "bot" / "eski.py").write_text("x = 1\n")  # yangi versiyada yo'q — o'chiriladi

    async def go():
        gh = FakeGitHub(release=None, branch_version="1.3.0", branch_zip=branch_zip)
        release = await gh.client().latest()
        assert release.source == "branch" and release.version == "1.3.0" and release.label == "GitHub «main» tarmog'i (release yo'q)"
        assert gh.api_calls() == [f"/repos/{REPO}/releases/latest", f"/repos/{REPO}/contents/VERSION"]
        release, data = await fetch_release(gh.client())
        assert data == branch_zip
        staged = upd.stage(data)
        assert staged.version == "1.3.0" and staged.notes == "tarmoqdan"
        assert "tests/test_x.py" in staged.skipped and "docs/TZ.md" in staged.skipped and "bot/app.py" in staged.files
        assert staged.changed == ["VERSION", "bot/models.py", "bot/eski.py (o'chiriladi)"]
        upd.discard(staged)
        gh.branch_version = "nomalum"
        with pytest.raises(GitHubError, match="VERSION fayli tushunarsiz"):
            await gh.client().latest()
        # release yo'q, tarmoq yo'q — token emas, GITHUB_BRANCH ni tekshirish so'raladi
        gh.branch_exists = False
        with pytest.raises(GitHubError, match="GITHUB_BRANCH") as info:
            await gh.client().latest()
        assert "GITHUB_TOKEN" not in str(info.value)
        with pytest.raises(GitHubError, match="GITHUB_TOKEN"):  # yopiq repo tokensiz — baribir token haqida
            await gh.client(token=None).latest()
    run(go())


# ----------------------------------------------------------------------------- oqim


def test_github_update_flow(tmp_path):
    async def go():
        env = await Env().start(tmp_path, github_repo=REPO)
        app, upd = env.app_dir, env.ctx.updater
        asset = release_zip(app, "1.2.0", marker="gh12",
                            extra={"UPDATE_NOTES.md": "Yangi: GitHub orqali yangilash", "tests/test_x.py": "x = 1\n"})
        gh = FakeGitHub(release=release_json("1.2.0", asset="claude-telegram-bot-v1.2.0.zip", size=len(asset),
                                             notes="GitHub izohi"), asset=asset)
        env.ctx.github = gh.client()

        def sent():
            return env.session.of("SendMessage")[-1]

        try:
            # /yangilash — .zip yo'li o'z joyida, qo'shimcha GitHub tugmalari
            await env.send_text("/yangilash")
            assert "v1.1.0" in sent().text and ".zip" in sent().text and REPO in sent().text
            buttons = [b.callback_data for b in sent().reply_markup.inline_keyboard[0]]
            assert buttons == [CALLBACK_GITHUB_INSTALL, CALLBACK_GITHUB_CHECK]

            await env.send_text("/yangilash tekshir")
            assert sent().text.startswith("🆕 Yangi versiya bor: v1.2.0 (joriy v1.1.0)") and "/yangilash github" in sent().text
            assert not any(r.url.host != "api.github.com" for r in gh.requests)  # tekshiruv yuklamaydi

            await env.send_text("/yangilash github", user_id=GUEST)
            await env.db.add_member(GUEST, "Ali", None)
            await env.send_text("/yangilash github", user_id=GUEST)
            assert "faqat bot egasi" in sent().text
            before = len(gh.requests)
            for data in (CALLBACK_GITHUB_INSTALL, CALLBACK_GITHUB_CHECK):
                await env.press(data, user_id=GUEST)
                assert env.session.of("AnswerCallbackQuery")[-1].text == "⛔ Ruxsat yo'q"
            assert len(gh.requests) == before and GUEST not in env.ctx.staged

            env.ctx.awaiting_update.clear()
            await env.send_text("/yangilash gitub")
            assert sent().text.startswith("Tushunarsiz parametr") and OWNER not in env.ctx.awaiting_update

            await env.send_text("/yangilash github")
            assert "GitHub'dan yuklanmoqda" in sent().text
            edits = [c.text for c in env.session.of("EditMessageText")]
            assert any("tekshirilmoqda" in t for t in edits)
            summary = env.last_edit()
            assert "v1.1.0 → <b>v1.2.0</b>" in summary.text
            assert "Manba: GitHub Release v1.2.0 (claude-telegram-bot-v1.2.0.zip)" in summary.text
            assert "O'zgargan fayllar (2): VERSION, bot/models.py" in summary.text
            assert "Yangi: GitHub orqali yangilash" in summary.text and "launcher.py" in summary.text
            assert "GitHub izohi" not in summary.text  # arxivdagi UPDATE_NOTES ustun
            ok_data, no_data = (b.callback_data for b in summary.reply_markup.inline_keyboard[0])
            assert ok_data.startswith("up:ok:") and no_data.startswith("up:no:")
            assert env.ctx.staged[OWNER].source.startswith("GitHub Release")

            await env.press(ok_data)
            assert env.ctx.restart_requested and "v1.2.0 o'rnatildi" in env.last_edit().text
            assert upd.version == "1.2.0" and "RELEASE_MARKER = 'gh12'" in (app / "bot" / "models.py").read_text()
            assert not (app / "tests").exists() and not (app / ".env").exists()
            assert (upd.backups()[0] / "VERSION").read_text().strip() == "1.1.0"
            env.session.clear()
            await bot_app.announce_update(env.bot, env.ctx)
            assert sent().text.startswith("✅ Bot yangilandi: v1.1.0 → v1.2.0")

            # endi eng so'nggi versiyada — tugma orqali tekshirish
            await env.press(CALLBACK_GITHUB_CHECK)
            assert sent().text.startswith("✅ Siz eng so'nggi versiyadasiz: v1.2.0")
            # xuddi shu versiyani qayta o'rnatish taklifi
            env.ctx.restart_requested = False
            await env.press(CALLBACK_GITHUB_INSTALL)
            assert "allaqachon o'rnatilgan" in env.last_edit().text and "Fayllar joriy versiya bilan bir xil" in env.last_edit().text
            await env.press(env.last_edit().reply_markup.inline_keyboard[0][1].callback_data)  # bekor
            assert "Bekor qilindi" in env.last_edit().text and OWNER not in env.ctx.staged

            # GitHub'dagi versiya eskiroq — ogohlantirish
            old = release_zip(app, "1.0.5")
            gh.release, gh.asset = release_json("1.0.5", asset="claude-telegram-bot-v1.0.5.zip", size=len(old)), old
            await env.send_text("/yangilash tekshir")
            assert sent().text.startswith("ℹ️ GitHub'dagi versiya (v1.0.5) joriy v1.2.0 dan eski")
            await env.send_text("/yangilash github")
            assert "joriy versiyadan eski" in env.last_edit().text
            await env.send_text("/bekor")
            assert OWNER not in env.ctx.staged

            # buzuq release — rad etiladi, joriy versiya o'zgarmaydi
            bad = release_zip(app, "1.3.0", extra={"bot/broken.py": "def x(:\n"})
            gh.release, gh.asset = release_json("1.3.0", asset="claude-telegram-bot-v1.3.0.zip", size=len(bad)), bad
            await env.send_text("/yangilash github")
            assert "GitHub'dan yangilab bo'lmadi: Sintaksis xatosi: bot/broken.py" in env.last_edit().text
            assert "Joriy versiya o'zgarmadi" in env.last_edit().text and upd.version == "1.2.0"
            assert not list((tmp_path / "data" / "update").glob("staging-*"))

            # GitHub xatolari
            gh.rate_limited = True
            await env.send_text("/yangilash github")
            assert "so'rovlar chegarasi" in env.last_edit().text
            await env.send_text("/yangilash tekshir")
            assert sent().text.startswith("❌ GitHub tekshirilmadi: GitHub so'rovlar chegarasi")
            gh.rate_limited = False
            env.ctx.github = gh.client(token=None)  # yopiq repo, token yo'q
            await env.send_text("/yangilash github")
            assert "GITHUB_TOKEN" in env.last_edit().text and "o'rnatish" not in env.last_edit().text.lower()

            # bir vaqtda ikkinchi so'rov
            env.ctx.github = gh.client()
            env.ctx.github_busy.add(OWNER)
            await env.send_text("/yangilash github")
            assert "davom etmoqda" in env.last_edit().text
            env.ctx.github_busy.clear()

            # yuklash paytida /bekor — yuklangan arxiv tashlab yuboriladi, tasdiq tugmasi chiqmaydi
            good = release_zip(app, "1.3.0", marker="gh13")
            gh.release, gh.asset = release_json("1.3.0", asset="claude-telegram-bot-v1.3.0.zip", size=len(good)), good
            gh.gate = asyncio.Event()
            task = asyncio.create_task(env.send_text("/yangilash github"))

            async def downloading():
                while not any(r.url.host == "objects.githubusercontent.com" for r in gh.requests[-3:]):
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(downloading(), timeout=10)
            await env.send_text("/bekor")
            gh.gate.set()
            await asyncio.wait_for(task, timeout=10)
            gh.gate = None
            assert "Bekor qilindi" in env.last_edit().text and env.last_edit().reply_markup is None
            assert OWNER not in env.ctx.staged and not env.ctx.github_busy and not env.ctx.github_cancelled
            assert not list((tmp_path / "data" / "update").glob("staging-*"))

            # holat xabarini tahrirlab bo'lmasa (o'chirilgan) — xulosa yangi xabar bo'lib keladi
            original = env.session.make_request

            async def gone(bot, method, timeout=None):
                if type(method).__name__ == "EditMessageText" and method.reply_markup is not None:
                    raise TelegramBadRequest(method=method, message="Bad Request: message to edit not found")
                return await original(bot, method, timeout)

            env.session.make_request = gone
            await env.send_text("/yangilash github")
            env.session.make_request = original
            fallback = sent()
            assert "v1.2.0 → <b>v1.3.0</b>" in fallback.text and fallback.reply_markup.inline_keyboard[0][0].callback_data.startswith("up:ok:")
            assert env.ctx.staged[OWNER].version == "1.3.0"
            await env.send_text("/bekor")

            # GitHub o'chiq
            env.ctx.github = None
            await env.send_text("/yangilash github")
            assert "GitHub'dan yangilash o'chiq" in sent().text and "GITHUB_REPO" in sent().text
            await env.send_text("/yangilash tekshir")
            assert "o'chiq" in sent().text
            await env.send_text("/yangilash")
            assert sent().reply_markup is None
            await env.send_text("/yordam")
            assert "/yangilash github" in sent().text and "/yangilash tekshir" in sent().text
        finally:
            await env.close()
    run(go())


def test_daily_check_notifies_once(tmp_path):
    async def go():
        env = await Env().start(tmp_path, github_repo=REPO, update_check=True)
        asset = release_zip(env.app_dir, "1.2.0", marker="d12")
        gh = FakeGitHub(release=release_json("1.2.0", asset="claude-telegram-bot-v1.2.0.zip", size=len(asset), notes="Izoh qatori"), asset=asset)
        env.ctx.github = gh.client()
        tz = "Asia/Tashkent"

        def today():  # yarim tunda ham to'g'ri bo'lsin — har safar qayta hisoblanadi
            return datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d")

        async def check(version="1.1.0"):
            return await scheduler.check_update(env.bot, env.db, env.ctx.github, version, [OWNER], tz)

        try:
            release = await check()
            assert release is not None and release.version == "1.2.0"
            notice = env.session.of("SendMessage")[-1]
            assert notice.text.startswith("🆕 GitHub'da yangi versiya: v1.2.0 (joriy v1.1.0)") and "Izoh qatori" in notice.text
            install, link = notice.reply_markup.inline_keyboard[0]
            assert install.callback_data == CALLBACK_GITHUB_INSTALL and link.url.endswith("/releases/tag/v1.2.0")
            assert await env.db.get_config("update:notified") == "1.2.0"
            assert await env.db.get_config("update:last_check") == today()
            assert not await scheduler.missed_update_check(env.db, tz, "00:00")

            # xuddi shu versiya haqida qayta aytilmaydi
            assert (await check()).version == "1.2.0"
            assert len(env.session.of("SendMessage")) == 1
            # yangisi yo'q — xabar yo'q
            assert await check("1.2.0") is None and await check("1.5.0") is None
            assert len(env.session.of("SendMessage")) == 1
            # GitHub ishlamasa — jim, tekshiruv sanasi yangilanmaydi
            await env.db.set_config("update:last_check", "2000-01-01")
            gh.down = True
            assert await check() is None
            assert await env.db.get_config("update:last_check") == "2000-01-01"
            assert await scheduler.missed_update_check(env.db, tz, "00:00")
            gh.down = False

            # xabardagi tugma odatdagi tasdiq oqimini ochadi; arxivda izoh yo'q — release izohi ko'rsatiladi
            await env.press(CALLBACK_GITHUB_INSTALL)
            assert "v1.1.0 → <b>v1.2.0</b>" in env.last_edit().text and env.ctx.staged[OWNER].version == "1.2.0"
            assert "Izoh qatori" in env.last_edit().text
            await env.send_text("/bekor")

            # Telegram xabarni qabul qilmasa — «aytildi» deb belgilanmaydi, keyingi tekshiruvda yana urinadi
            gh.release = release_json("1.2.5", asset="claude-telegram-bot-v1.2.5.zip", size=5)
            original = env.session.make_request

            async def refuse(bot, method, timeout=None):
                if type(method).__name__ == "SendMessage":
                    raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
                return await original(bot, method, timeout)

            env.session.make_request = refuse
            assert (await check()).version == "1.2.5"
            assert await env.db.get_config("update:notified") == "1.2.0"
            env.session.make_request = original
            assert (await check()).version == "1.2.5"
            assert env.session.of("SendMessage")[-1].text.startswith("🆕 GitHub'da yangi versiya: v1.2.5")
            assert await env.db.get_config("update:notified") == "1.2.5"

            # yanada yangisi chiqsa — yana bir marta
            gh.release = release_json("1.3.0", asset="claude-telegram-bot-v1.3.0.zip", size=5)
            assert (await check()).version == "1.3.0"
            assert env.session.of("SendMessage")[-1].text.startswith("🆕 GitHub'da yangi versiya: v1.3.0")
            assert await env.db.get_config("update:notified") == "1.3.0"

            # kunlik ish: zaxira vaqtida; zaxira o'chiq bo'lsa 03:30; UPDATE_CHECK=off — yo'q
            await env.db.set_config("update:last_check", today())  # bugun tekshirilgan — kechikkan tekshiruv yo'q
            job = bot_app.start_update_check_job(env.bot, env.ctx)
            assert job is not None and job.name == "update-check" and (job.hour, job.minute) == (3, 30)
            await job.stop()
            env.settings = env.ctx.settings = env.ctx.settings.__class__(**{**env.ctx.settings.__dict__, "backup_time": "22:15"})
            job = bot_app.start_update_check_job(env.bot, env.ctx)
            assert (job.hour, job.minute) == (22, 15)
            await job.stop()
            env.ctx.settings = env.ctx.settings.__class__(**{**env.ctx.settings.__dict__, "update_check": False})
            assert bot_app.start_update_check_job(env.bot, env.ctx) is None
            env.ctx.settings = env.ctx.settings.__class__(**{**env.ctx.settings.__dict__, "update_check": True})
            env.ctx.github = None
            assert bot_app.start_update_check_job(env.bot, env.ctx) is None

            # kechikkan tekshiruv: bot ishga tushganda darhol
            env.ctx.github = gh.client()
            gh.release = release_json("1.4.0", asset="claude-telegram-bot-v1.4.0.zip", size=5)
            await env.db.set_config("update:last_check", "2000-01-01")
            env.ctx.settings = env.ctx.settings.__class__(**{**env.ctx.settings.__dict__, "backup_time": "00:00"})
            job = bot_app.start_update_check_job(env.bot, env.ctx)
            await asyncio.wait_for(job.catch_up_task, timeout=10)
            await job.stop()
            assert env.session.of("SendMessage")[-1].text.startswith("🆕 GitHub'da yangi versiya: v1.4.0")
            assert await env.db.get_config("update:last_check") == today()

            # to'xtatilganda kechikkan tekshiruv ham bekor qilinadi (bot yopilgandan keyin ishlamaydi)
            await env.db.set_config("update:last_check", "2000-01-01")
            gh.api_gate = asyncio.Event()  # GitHub javob bermay turibdi
            job = bot_app.start_update_check_job(env.bot, env.ctx)
            await asyncio.sleep(0.05)
            assert not job.catch_up_task.done()
            await job.stop()
            assert job.catch_up_task.cancelled()
            gh.api_gate = None
        finally:
            await env.close()
    run(go())


def test_miniapp_admin_github(tmp_path):
    async def go():
        app = await App().start(tmp_path)
        env = app.env
        asset = release_zip(env.app_dir, "1.2.0", marker="web12")
        gh = FakeGitHub(release=release_json("1.2.0", asset="claude-telegram-bot-v1.2.0.zip", size=len(asset)), asset=asset)
        try:
            assert (await app.get("/api/admin"))["github_repo"] is None
            err = await app.post("/api/admin/github", {}, status=409)
            assert "o'chiq" in err["message"]

            env.ctx.github = gh.client()
            admin = await app.get("/api/admin")
            assert admin["github_repo"] == REPO and admin["update_check"] is False
            check = await app.post("/api/admin/github", {"action": "check"})
            assert check["message"].startswith("🆕 Yangi versiya bor: v1.2.0") and check["current"] == "1.1.0"

            env.session.clear()
            res = await app.post("/api/admin/github", {})
            assert (res["version"], res["current"], res["changed"]) == ("1.2.0", "1.1.0", 2)
            confirm = env.session.of("SendMessage")[-1]
            assert confirm.chat_id == OWNER and "v1.1.0 → <b>v1.2.0</b>" in confirm.text and "Manba: GitHub Release v1.2.0" in confirm.text
            assert confirm.reply_markup.inline_keyboard[0][0].callback_data.startswith("up:ok:")
            assert env.ctx.staged[OWNER].version == "1.2.0"

            gh.rate_limited = True
            err = await app.post("/api/admin/github", {}, status=409)
            assert "chegarasi" in err["message"]
            gh.rate_limited = False

            bad = release_zip(env.app_dir, "1.3.0", extra={"bot/broken.py": "def x(:\n"})
            gh.release, gh.asset = release_json("1.3.0", asset="claude-telegram-bot-v1.3.0.zip", size=len(bad)), bad
            err = await app.post("/api/admin/github", {}, status=409)
            assert "Sintaksis xatosi: bot/broken.py" in err["message"]
            env.ctx.github_busy.add(OWNER)
            err = await app.post("/api/admin/github", {}, status=409)
            assert "davom etmoqda" in err["message"]
            env.ctx.github_busy.clear()

            await app.post("/api/admin/github", {}, user_id=777, status=403)
            await env.db.add_member(MEMBER, "A'zo", None)  # parol bilan kirgan a'zo — admin emas
            await app.post("/api/admin/github", {}, user_id=MEMBER, status=403)
        finally:
            await app.close()
    run(go())


# ----------------------------------------------------------------------------- sozlamalar


def test_config_github_settings(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:abc")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("OWNER_IDS", "5591487950")
    for name in ("GITHUB_REPO", "GITHUB_TOKEN", "GITHUB_BRANCH", "UPDATE_CHECK"):
        monkeypatch.delenv(name, raising=False)
    s = load_settings()
    assert (s.github_repo, s.github_token, s.github_branch, s.update_check) == (DEFAULT_GITHUB_REPO, None, "main", True)

    monkeypatch.setenv("GITHUB_REPO", "off")
    assert load_settings().github_repo is None
    monkeypatch.setenv("GITHUB_REPO", "https://github.com/x/y")
    with pytest.raises(ConfigError, match="GITHUB_REPO"):
        load_settings()
    monkeypatch.setenv("GITHUB_REPO", "Egasi/bot-repo")
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setenv("GITHUB_BRANCH", "release/v2")
    monkeypatch.setenv("UPDATE_CHECK", "off")
    s = load_settings()
    assert (s.github_repo, s.github_token, s.github_branch, s.update_check) == ("Egasi/bot-repo", TOKEN, "release/v2", False)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_bo'shliq bilan")
    with pytest.raises(ConfigError, match="GITHUB_TOKEN"):
        load_settings()
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    for bad_branch in ("ikki so'z", "../boshqa"):
        monkeypatch.setenv("GITHUB_BRANCH", bad_branch)
        with pytest.raises(ConfigError, match="GITHUB_BRANCH"):
            load_settings()
