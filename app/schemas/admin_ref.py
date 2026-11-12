"""原站资源镜像（应用中心 / 婚创学苑 / 红娘课堂 / 用好系统 / 云端图库）后台契约。

数据来源：原站 www.xuanshiai.com 一次性镜像入库（ref_* 表），全部只读。
文件资源：图片/文档镜像到服务器 /refsite/ 静态目录；视频保留原站播放 URL。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


RefGuideType = Literal["Video", "Article", "FileToGet", "Attachment", "Link"]


class RefTeacher(BaseModel):
    id: int
    ref_id: int
    name: str
    avatar_url: str | None = None
    intro: str | None = None
    course_count: int = 0


class RefProduct(BaseModel):
    id: int
    ref_id: int
    name: str
    category: str | None = None
    intro: str | None = None
    detail: str | None = None
    cover_url: str | None = None
    album_url: str | None = None
    price: float | None = None
    purchased: bool = False


class RefGuideItem(BaseModel):
    id: int
    ref_id: int
    title: str
    guide_type: RefGuideType
    category_name: str | None = None
    teacher_ref_id: int | None = None
    teacher_name: str | None = None
    cover_url: str | None = None
    duration: int = 0
    likes: int = 0
    view_times: int = 0
    is_top: bool = False
    title_bold: bool = False
    title_color: str | None = None
    on_shelf: bool = True
    created_at: datetime | None = None


class RefGuideDetail(RefGuideItem):
    url: str | None = None
    content: str | None = None


class RefGuidePage(BaseModel):
    items: list[RefGuideItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class RefGuideCategories(BaseModel):
    """红娘课堂/婚创学苑左侧栏目 + 讲师（一次返回，避免多次请求）。"""

    categories: list[str] = []
    teachers: list[RefTeacher] = []


class RefInstructionItem(BaseModel):
    id: int
    ref_id: int
    title: str
    content: str | None = None
    created_at: datetime | None = None


class RefMaterialItem(BaseModel):
    id: int
    ref_id: int
    title: str | None = None
    category_name: str
    sub_category_name: str | None = None
    img_url: str


class RefMaterialCategory(BaseModel):
    name: str
    subs: list[str] = []
    total: int = 0


class RefMaterialPage(BaseModel):
    items: list[RefMaterialItem]
    page: int
    page_size: int
    total: int
    has_more: bool
    categories: list[RefMaterialCategory] = []
