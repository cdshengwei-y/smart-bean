from __future__ import annotations
import json
from pathlib import Path
from threading import Lock

DEFAULT_TYPES = {"software_development":"软件开发","product":"产品管理","project":"项目管理","sales":"销售","marketing":"市场运营","customer_service":"客户服务","hr_admin":"行政人事","finance":"财务","education":"教育培训","healthcare":"医疗健康"}
RECORDING_SCENES = {"work":"普通工作内容", "meeting":"会议内容", "all":"全部内容"}

class WorkProfileStore:
    def __init__(self, path: Path):
        self.path, self._lock = path, Lock(); path.parent.mkdir(parents=True, exist_ok=True)
    def _read(self):
        try: return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError): return {}
    def get(self, device_id: str) -> dict:
        data = self._read()
        default = {"enabled":False,"optimize_enabled":True,"recording_scene":"work","work_types":[],"custom_description":"","excluded_content":""}
        value = data.get("__default__", default)
        if not isinstance(value, dict):
            return default
        return {**default, **value, "recording_scene": str(value.get("recording_scene", "work")) if str(value.get("recording_scene", "work")) in RECORDING_SCENES else "work"}
    def put(self, device_id: str, profile: dict) -> dict:
        types=[str(x) for x in profile.get("work_types",[]) if str(x) in DEFAULT_TYPES or str(x)=="custom"]
        scene = str(profile.get("recording_scene", "work"))
        if scene not in RECORDING_SCENES:
            scene = "work"
        clean={"enabled":bool(profile.get("enabled",False)),"optimize_enabled":True,"recording_scene":scene,"work_types":types,"custom_description":str(profile.get("custom_description","")).strip()[:2000],"excluded_content":str(profile.get("excluded_content","")).strip()[:2000]}
        with self._lock:
            data=self._read(); data[device_id]=clean; self.path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
        return clean
