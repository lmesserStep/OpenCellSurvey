import asyncio
import logging
from typing import Callable, Awaitable, Optional

from models import ScanConfig, SurveyPoint
from parser import parse_line
import config as cfg

logger = logging.getLogger(__name__)

OnData = Callable[[SurveyPoint], Awaitable[None]]
OnLog  = Callable[[str],         Awaitable[None]]
OnStop = Callable[[],            Awaitable[None]]


class SurveyRunner:
    def __init__(self) -> None:
        self._process: Optional[asyncio.subprocess.Process] = None
        self._running = False
        self._on_data: Optional[OnData] = None
        self._on_log:  Optional[OnLog]  = None
        self._on_stop: Optional[OnStop] = None

    # ── public ────────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    def register_data_callback(self, cb: OnData) -> None:
        self._on_data = cb

    def register_log_callback(self, cb: OnLog) -> None:
        self._on_log = cb

    def register_stop_callback(self, cb: OnStop) -> None:
        self._on_stop = cb

    async def start(self, scan_cfg: ScanConfig) -> None:
        if self._running:
            raise RuntimeError("Survey already running")
        cmd = self._build_command(scan_cfg)
        logger.info("Launching: %s", " ".join(cmd))
        await self._emit_log(f"$ {' '.join(cmd)}")
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._running = True
        asyncio.create_task(self._read_stdout())
        asyncio.create_task(self._read_stderr())

    async def stop(self) -> None:
        if self._process and self._running:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
            self._running = False
            logger.info("Survey stopped")
            await self._emit_log("[system] Survey stopped")

    # ── private ───────────────────────────────────────────────────────────────

    def _build_command(self, scan_cfg: ScanConfig) -> list[str]:
        cmd = [scan_cfg.binary_path, "-b", str(scan_cfg.band)]
        if scan_cfg.pci is not None:
            cmd += ["-p", str(scan_cfg.pci)]
        if scan_cfg.mode == "fast":
            cmd += ["-f"]
        if scan_cfg.frame_type == "TDD" and scan_cfg.bandwidth_mhz:
            prb = cfg.TDD_BANDWIDTH_PRB.get(scan_cfg.bandwidth_mhz, 50)
            cmd += ["--prb", str(prb)]
        return cmd

    async def _read_stdout(self) -> None:
        try:
            while self._process and not self._process.stdout.at_eof():
                raw = await self._process.stdout.readline()
                if not raw:
                    break
                text = raw.decode("utf-8", errors="replace")
                point = parse_line(text)
                if point and self._on_data:
                    await self._on_data(point)
                await self._emit_log(f"[stdout] {text.rstrip()}")
        except Exception as exc:
            logger.error("stdout reader: %s", exc)
        finally:
            self._running = False
            await self._emit_log("[system] Binary process ended")
            if self._on_stop:
                await self._on_stop()

    async def _read_stderr(self) -> None:
        try:
            while self._process and not self._process.stderr.at_eof():
                raw = await self._process.stderr.readline()
                if not raw:
                    break
                text = raw.decode("utf-8", errors="replace")
                await self._emit_log(f"[stderr] {text.rstrip()}")
        except Exception as exc:
            logger.error("stderr reader: %s", exc)

    async def _emit_log(self, msg: str) -> None:
        if self._on_log:
            await self._on_log(msg)
