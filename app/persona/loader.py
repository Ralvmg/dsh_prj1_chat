"""人设加载器 —— 从 YAML 文件加载人设，支持热切换

人设文件格式（YAML）：
    name: 鲸鱼娘·大肥鱼
    avatar: 🐋
    description: 一句话描述
    system_prompt: |
      这里是人设正文...
    config:
      temperature: 0.9
      max_tokens: 500
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 尝试导入 yaml，没有就用极简解析器
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False
    logger.warning("PyYAML 未安装，将使用极简 YAML 解析器（仅支持本项目的固定格式）")


class Persona:
    """一个人设"""

    def __init__(self, name: str, system_prompt: str, avatar: str = "",
                 description: str = "", config: dict = None):
        self.name = name
        self.system_prompt = system_prompt
        self.avatar = avatar
        self.description = description
        self.config = config or {}

    def __repr__(self):
        return f"<Persona {self.name}>"

    @property
    def temperature(self) -> float:
        return self.config.get("temperature", 0.9)

    @property
    def max_tokens(self) -> int:
        return self.config.get("max_tokens", 500)


def _parse_simple_yaml(text: str) -> dict:
    """极简 YAML 解析器 —— 只支持本项目用到的格式

    支持的格式：
        key: value
        key: |
          多行文本
    """
    result = {}
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.strip().startswith("#"):
            i += 1
            continue

        # 顶层 key
        if not line.startswith((" ", "\t")) and ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()

            if value in ("|", "|-", ">", ">-"):
                # 多行文本块
                block = []
                i += 1
                while i < len(lines):
                    l = lines[i]
                    if l.strip() == "":
                        block.append("")
                        i += 1
                        continue
                    if not l.startswith((" ", "\t")):
                        break
                    block.append(l)
                    i += 1
                # 去掉公共缩进
                non_empty = [b for b in block if b.strip()]
                if non_empty:
                    indent = min(len(b) - len(b.lstrip()) for b in non_empty)
                    block = [b[indent:] if len(b) >= indent else b for b in block]
                result[key] = "\n".join(block).strip()
                continue
            else:
                # 去掉引号
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                result[key] = value
        i += 1
    return result


def load_persona_file(path: Path) -> Optional[Persona]:
    """从文件加载人设"""
    if not path.exists():
        logger.error(f"人设文件不存在: {path}")
        return None

    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"读取人设文件失败 {path}: {e}")
        return None

    if HAS_YAML:
        try:
            data = yaml.safe_load(text) or {}
        except Exception as e:
            logger.error(f"YAML 解析失败 {path}: {e}")
            return None
    else:
        data = _parse_simple_yaml(text)

    name = data.get("name") or path.stem
    system_prompt = data.get("system_prompt", "")
    if not system_prompt:
        logger.error(f"人设文件缺少 system_prompt: {path}")
        return None

    return Persona(
        name=name,
        system_prompt=system_prompt,
        avatar=data.get("avatar", ""),
        description=data.get("description", ""),
        config=data.get("config", {}) or {},
    )


class PersonaManager:
    """人设管理器 —— 管理内置和自定义人设，所有人格叠加共享基线"""

    def __init__(self, builtin_dir: str, user_dir: str):
        self.builtin_dir = Path(builtin_dir)
        self.user_dir = Path(user_dir)
        self._cache: dict[str, Persona] = {}
        self._current: Optional[Persona] = None
        self._current_key: str = ""
        self._baseline = self._load_baseline()

    def _load_baseline(self) -> str:
        """加载人格基线（baseline.md），所有人格共享"""
        # 基线文件在 builtin 目录的上一级
        for candidate in (self.builtin_dir.parent / "baseline.md",
                          self.builtin_dir / "baseline.md"):
            if candidate.exists():
                try:
                    text = candidate.read_text(encoding="utf-8").strip()
                    if text:
                        logger.info(f"已加载人格基线: {candidate.name} ({len(text)} 字)")
                        return text
                except Exception as e:
                    logger.warning(f"读取基线失败 {candidate}: {e}")
        logger.warning("未找到 baseline.md，人格将不叠加基线")
        return ""

    def _compose(self, p: Persona) -> Persona:
        """把基线和人格正文组合成完整 system_prompt"""
        if not self._baseline:
            return p
        full = f"{self._baseline}\n\n{'=' * 20} 人格正文 {'=' * 20}\n\n{p.system_prompt}"
        return Persona(
            name=p.name,
            system_prompt=full,
            avatar=p.avatar,
            description=p.description,
            config=p.config,
        )

    def list_all(self) -> dict[str, dict]:
        """列出所有人设（内置 + 自定义）"""
        result = {}
        for d, source in ((self.builtin_dir, "builtin"), (self.user_dir, "user")):
            if not d.exists():
                continue
            for f in sorted(d.glob("*.yml")) + sorted(d.glob("*.yaml")):
                try:
                    p = self._load(f)
                    if p:
                        result[f.stem] = {
                            "name": p.name,
                            "avatar": p.avatar,
                            "description": p.description,
                            "source": source,
                        }
                except Exception as e:
                    logger.warning(f"加载人设失败 {f}: {e}")
        return result

    def _load(self, path: Path) -> Optional[Persona]:
        key = str(path)
        if key in self._cache:
            return self._cache[key]
        p = load_persona_file(path)
        if p:
            self._cache[key] = p
        return p

    def get(self, name: str) -> Optional[Persona]:
        """按名称获取人设，优先自定义目录"""
        for d in (self.user_dir, self.builtin_dir):
            for ext in (".yml", ".yaml"):
                f = d / f"{name}{ext}"
                if f.exists():
                    return self._load(f)
        return None

    def set_current(self, name: str) -> bool:
        """切换当前人设（组合基线后生效）"""
        p = self.get(name)
        if not p:
            logger.error(f"人设不存在: {name}")
            return False
        self._current = self._compose(p)
        self._current_key = name
        logger.info(f"已切换人设: {p.name} ({name})，叠加基线 {len(self._baseline)} 字")
        return True

    @property
    def current(self) -> Optional[Persona]:
        return self._current

    @property
    def current_key(self) -> str:
        """当前人设的 key（文件名），用于前端高亮"""
        return getattr(self, "_current_key", "")

    def reload(self):
        """清空缓存，重新加载"""
        self._cache.clear()
