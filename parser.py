import json
import logging
from typing import Optional
from datetime import datetime, timezone

from models import SurveyPoint

logger = logging.getLogger(__name__)


def parse_line(line: str) -> Optional[SurveyPoint]:
    """Parse a single JSON line emitted by the cell_monitor binary."""
    line = line.strip()
    if not line:
        return None
    try:
        data = json.loads(line)
        data["timestamp"] = datetime.now(timezone.utc).isoformat()
        return SurveyPoint(**data)
    except json.JSONDecodeError as exc:
        logger.warning("JSON parse error: %s | raw: %r", exc, line)
        return None
    except Exception as exc:
        logger.warning("Model validation error: %s | raw: %r", exc, line)
        return None
