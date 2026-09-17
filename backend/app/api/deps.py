from __future__ import annotations

from fastapi import Request

from app.agent.investigations import InvestigationService
from app.agent.service import AnalystService
from app.core.config import Settings
from app.db import Database
from app.services.activity import ActivityService
from app.services.boards import BoardService
from app.services.briefings import BriefingService
from app.services.comments import CommentService
from app.services.datasets import DatasetService
from app.services.monitors import MonitorService
from app.services.notifications import NotificationService
from app.services.sharing import ShareService
from app.services.sources import SourceService


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_datasets(request: Request) -> DatasetService:
    return request.app.state.datasets


def get_analyst(request: Request) -> AnalystService:
    return request.app.state.analyst


def get_boards(request: Request) -> BoardService:
    return request.app.state.boards


def get_shares(request: Request) -> ShareService:
    return request.app.state.shares


def get_monitors(request: Request) -> MonitorService:
    return request.app.state.monitors


def get_notifier(request: Request) -> NotificationService:
    return request.app.state.notifier


def get_sources(request: Request) -> SourceService:
    return request.app.state.sources


def get_comments(request: Request) -> CommentService:
    return request.app.state.comments


def get_activity(request: Request) -> ActivityService:
    return request.app.state.activity


def get_briefings(request: Request) -> BriefingService:
    return request.app.state.briefings


def get_investigations(request: Request) -> InvestigationService:
    return request.app.state.investigations
