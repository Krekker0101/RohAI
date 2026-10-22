"""Public reviewed evidence, source selection and generated-answer format checks."""

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Page = Literal[
    "overview", "video", "analytics", "comparison", "calibration", "intelligence", "operations"
]
Level = Literal["auto", "simple", "standard", "technical"]
Language = Literal["ru", "en", "tg"]


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    status: Literal["implemented", "partial", "experimental", "planned", "unconfirmed"]
    keywords: str
    simple: str
    standard: str
    technical: str
    sources: list[str] = Field(min_length=1)
    english: str = ""
    references: list[str] = Field(default_factory=list)


class Knowledge:
    def __init__(self, path: Path) -> None:
        path = path.resolve()
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("public_only") is not True:
            raise ValueError("Pilot knowledge must be public")
        self.facts = {fact.id: fact for fact in (Fact.model_validate(x) for x in data["facts"])}
        if len(self.facts) != len(data["facts"]):
            raise ValueError("Duplicate knowledge identifiers")
        self.version = hashlib.sha256(path.read_bytes()).hexdigest()
        # Knowledge source paths are repository-relative. Resolve them from the
        # repository root (the knowledge file lives under configs/pilot), not
        # from the process working directory used to launch the backend.
        repository_root = path.parents[2]
        for fact in self.facts.values():
            if any(identity not in self.facts for identity in fact.references):
                raise ValueError("Invalid knowledge reference")
        self.source_hashes: dict[str, str] = {}
        for fact in self.facts.values():
            for source in fact.sources:
                if (
                    ".." in Path(source).parts
                    or Path(source).is_absolute()
                    or source.startswith(".")
                ):
                    raise ValueError("Non-public knowledge source")
                if (
                    not source.startswith(
                        ("apps/", "configs/", "packages/", "docs/", "scripts/", "tests/")
                    )
                    and source != "README.md"
                ):
                    raise ValueError("Source outside public allowlist")
                source_path = repository_root / source
                if not source_path.is_file():
                    raise ValueError(f"Knowledge source does not exist: {source}")
                self.source_hashes[source] = hashlib.sha256(source_path.read_bytes()).hexdigest()

    def retrieve(self, question: str, page: Page, previous: list[str]) -> list[Fact]:
        lower = question.lower()
        if re.search(
            r"датасет|dataset|маҷм[ӯу]аи?\s+(?:дода|маълумот)|"
            r"маҷм[ӯу]аи?\s+омӯзиш|training data",
            lower,
        ):
            return [self.facts["training"]]
        if re.search(r"тоҷик|таджик|tajik", lower) and re.search(
            r"гап|сухан|овоз|забон|голос|speech|voice", lower
        ):
            return [self.facts["pilot"]]
        if re.search(r"поворот|rotat", lower):
            return [self.facts["aerial"]]
        if re.search(r"confidence|уверен|track.?id|\bID\b|км/ч|km/h", question, re.I):
            return [self.facts["tracking"]]
        if re.search(r"истори|аудио|history|audio", lower) and re.search(
            r"хран|сохран|save|stor|приват|privacy", lower
        ):
            return [self.facts["auth"]]
        if re.search(r"обуч|train", lower):
            return [self.facts["training"]]
        if re.search(r"failed|сбой|ошибк.{0,20}анализ", lower):
            return [self.facts["errors"]]
        if re.search(r"светофор|traffic light|signal", lower) and re.search(
            r"видео|цвет|распозна|video|color|recogn", lower
        ):
            return [self.facts["lights"]]
        if re.search(r"таджик|tajik|тоҷик", lower) and re.search(
            r"голос|говор|пилот|язык|pilot|speech|speak|voice", lower
        ):
            return [self.facts["pilot"]]
        if re.search(r"(км/ч|km/h|километр.*час)", lower):
            return [self.facts["tracking"]]
        if "fixed-time" in lower and not re.search(r"сравн|выигрыш|benchmark|compar", lower):
            return [self.facts["control"]]
        ordinal = re.search(r"(перв|втор|трет|first|second|third)", lower)
        if previous and ordinal:
            word = ordinal.group()
            index = 0 if word in ("перв", "first") else 1 if word in ("втор", "second") else 2
            return [self.facts[previous[index]]] if index < len(previous) else []
        words = re.findall(r"[а-яёғқҳҷӯӣa-z0-9]+", lower)
        excluded = {
            "что",
            "как",
            "это",
            "этот",
            "здесь",
            "можно",
            "пилот",
            "what",
            "how",
            "this",
            "the",
            "pilot",
            "про",
            "расскажи",
            "about",
            "rohai",
            "проект",
            "проекта",
            "проекте",
            "project",
            "дар",
            "барои",
            "чӣ",
            "чи",
            "метавонад",
            "метавони",
            "гуфта",
            "бораи",
        }
        meaningful = [word for word in words if word not in excluded and len(word) >= 3]

        def score(fact: Fact) -> int:
            keys = fact.keywords.lower().split()
            return sum(
                3
                for word in meaningful
                if any(word.startswith(key) or key.startswith(word) for key in keys)
            )

        ordered = sorted(self.facts.values(), key=score, reverse=True)
        selected = [fact for fact in ordered if score(fact) > 0][:6]
        if (
            not selected
            and previous
            and any(
                word in lower
                for word in ("подробнее", "попроще", "это", "details", "simpler", "that")
            )
        ):
            selected = [self.facts[x] for x in previous[:3]]
        if not selected and any(
            word in lower
            for word in (
                "здесь",
                "график",
                "этот раздел",
                "this page",
                "this chart",
                "this section",
            )
        ):
            selected = [self.facts[page if page in self.facts else "analytics"]]
        if not selected and re.search(
            r"что.*(rohai|проект)|как работает.*(rohai|проект)|what is.*rohai|how.*rohai.*work",
            lower,
        ):
            selected = [self.facts["overview"]]
        if not selected and lower.strip(" !?.") in ("привет", "hello", "hi"):
            selected = [self.facts["overview"], self.facts["pilot"]]
        return selected

    def generation_content(self, fact: Fact, question: str, language: Language, level: str) -> str:
        """Supply public evidence at the requested detail, never manufacture an answer."""
        if language == "en":
            return fact.english
        if language == "tg":
            # Keep evidence intact; multilingual generators are instructed to answer in Tajik.
            evidence = fact.english or "\n".join(dict.fromkeys((fact.simple, fact.standard)))
            return (
                "Answer in standard Tajik (Тоҷикӣ), in Cyrillic. Use natural, grammatically "
                "correct Tajik wording and current Tajik spelling, including ғ, ӣ, қ, ӯ, ҳ, ҷ. "
                "Do not answer in Russian or transliteration. Keep product names and necessary "
                "technical terms unchanged. State only what the evidence supports; do not invent "
                "details. Be concise and directly answer the question.\n"
                "Verified project evidence:\n" + evidence
            )
        if level in ("simple", "standard", "technical"):
            return str(getattr(fact, level))
        if fact.id == "speed":
            return fact.simple if re.search(r"почему|why", question, re.I) else fact.standard
        if fact.id == "tracking" and re.search(r"км/ч|km/h", question, re.I):
            return fact.technical
        return "\n".join(dict.fromkeys((fact.simple, fact.standard)))

    def select_evidence(self, raw: str, candidates: list[Fact]) -> list[Fact]:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group() if match else raw)
        ids = data.get("ids")
        allowed = {fact.id: fact for fact in candidates}
        if (
            not isinstance(ids, list)
            or not ids
            or len(ids) > 3
            or any(not isinstance(x, str) or x not in allowed for x in ids)
        ):
            raise ValueError("PILOT_GROUNDING_FAILED")
        return [allowed[x] for x in dict.fromkeys(ids)]

    def validate_generated(
        self, raw: str, facts: list[Fact], language: Language, question: str = ""
    ) -> str:
        answer = re.sub(r"(?m)^\s*\d+[.)]\s+", "", raw.strip())
        if not answer or len(answer) > 2400 or re.search(r"[\x00-\x08\x0b\x0c]", answer):
            raise ValueError("PILOT_GENERATION_INVALID")
        if re.search(r"https?://|<[^>]+>|\[(system|assistant)\]", answer, re.I):
            raise ValueError("PILOT_GENERATION_INVALID")
        evidence = " ".join(
            " ".join((fact.simple, fact.standard, fact.technical, fact.english)) for fact in facts
        )
        if re.search(r"рынк|продукт|услуг|commercial|sales|market", answer, re.I) and not re.search(
            r"рынк|продукт|услуг|commercial|sales|market", evidence, re.I
        ):
            raise ValueError("PILOT_GENERATION_UNSUPPORTED")
        numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?%?", answer))
        supported = set(re.findall(r"\b\d+(?:[.,]\d+)?%?", evidence))
        if numbers - supported:
            raise ValueError("PILOT_GENERATION_UNSUPPORTED")
        for quoted in re.findall(r'[«"“`]([^«"“»”`]+)[»"”`]', answer):
            if quoted.casefold() not in evidence.casefold():
                raise ValueError("PILOT_GENERATION_UNSUPPORTED")
        # Reject observed dangerous reversals of the evidence's known limitations.
        caveat = re.search(
            r"не.{0,25}(подтверж|провер|доказ|распозна|определ|может|обуч|реализ|поддерж|доступ|измер|установ|извест)|"
            r"нет\s+(провер|размет)|проверенного\s+классификатора.{0,50}нет|"
            r"эксперимент|может\s+ошиба|неподтвержд|пока\s+нет|нет\s+подтвержд|"
            r"not\s+(verified|proven|validated|trained|implemented|supported)|no\s+verified|"
            r"experimental|can\s+be\s+wrong|does\s+not\s+prove",
            answer,
            re.I,
        )
        needs_caveat = any(fact.status == "unconfirmed" for fact in facts)
        if any(fact.status == "experimental" for fact in facts) and re.search(
            r"более\s+точн|повышает.{0,20}точност|улучшает.{0,20}точност|improves?\s+accuracy",
            answer,
            re.I,
        ):
            raise ValueError("PILOT_GENERATION_UNSUPPORTED")
        if re.search(
            r"огранич|точност|проверенн|проверен|гарант|limit|accuracy|verified|guarantee",
            question,
            re.I,
        ):
            needs_caveat |= any(fact.status == "experimental" for fact in facts)
        if re.search(r"точност|accuracy", question, re.I) and re.search(
            r"не доказы|не подтверж|not prove|not verified", evidence, re.I
        ):
            needs_caveat = True
        if needs_caveat and not caveat:
            raise ValueError("PILOT_GENERATION_UNSUPPORTED")
        if re.search(r"ограничений нет|без ограничений|no limitations", answer, re.I):
            raise ValueError("PILOT_GENERATION_UNSUPPORTED")
        if language == "tg":
            words = re.findall(r"[а-яёғқҳҷӯӣ]+", answer.casefold())
            if not words or not re.search(r"[ғқҳҷӯӣ]", answer, re.I):
                raise ValueError("PILOT_LANGUAGE_UNAVAILABLE")
            counts = {word: words.count(word) for word in set(words)}
            if len(words) >= 8 and max(counts.values()) / len(words) > 0.3:
                raise ValueError("PILOT_REPETITION")
            if len(words) >= 12 and len(set(words)) / len(words) < 0.4:
                raise ValueError("PILOT_REPETITION")
        if language == "ru" and not re.search(r"[а-яё]", answer, re.I):
            raise ValueError("PILOT_LANGUAGE_UNAVAILABLE")
        if language == "ru":
            prose = re.sub(r'[«"“`].*?[»"”`]', "", answer)
            if re.search(r"\b(while|because|using|formulates|with|and|the)\b", prose, re.I):
                raise ValueError("PILOT_LANGUAGE_UNAVAILABLE")
        if language == "en":
            # Quoted Russian UI names are allowed; the actual explanation must be English.
            prose = re.sub(r'[«"“].*?[»"”]', "", answer)
            latin = len(re.findall(r"[a-z]", prose, re.I))
            cyrillic = len(re.findall(r"[а-яё]", prose, re.I))
            if latin < 15 or cyrillic > latin:
                raise ValueError("PILOT_LANGUAGE_UNAVAILABLE")
        if re.search(r"[\u4e00-\u9fff]", answer):
            raise ValueError("PILOT_LANGUAGE_UNAVAILABLE")
        return answer


INJECTION = re.compile(
    r"ignore (all |previous |your )?(instructions|rules)|forget (your |previous )?instructions|"
    r"забудь.{0,24}(инструкц|правил)|игнорируй.{0,24}(инструкц|правил)|system prompt|"
    r"системн.{0,12}промпт|теперь ты|you are now|\.env|api.?key|api.?ключ",
    re.I,
)
OUTSIDE = re.compile(
    r"погод|weather|президент|новост|news|стихотвор|анекдот|шутк|"
    r"домашн.{0,10}задан|solve.{0,10}(equation|homework)|рецепт.{0,10}(суп|еды)|"
    r"write.{0,10}poem",
    re.I,
)


def topic_allowed(question: str) -> bool:
    return not INJECTION.search(question) and not OUTSIDE.search(question)
