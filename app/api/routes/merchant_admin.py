"""Merchant alliance (商家联盟) routes for the back office (M7-B)."""

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.merchant_admin import (
    MerchantCategoryCreate,
    MerchantCategoryItem,
    MerchantCategoryOrder,
    MerchantCategoryUpdate,
    MerchantCreate,
    MerchantItem,
    MerchantOption,
    MerchantOrderItem,
    MerchantOrderPage,
    MerchantOrderStatusUpdate,
    MerchantPage,
    MerchantProductCreate,
    MerchantProductItem,
    MerchantProductPage,
    MerchantProductUpdate,
    MerchantUpdate,
)
from app.services import merchant_admin as service

router = APIRouter(prefix="/admin/merchants")
category_router = APIRouter(prefix="/admin/merchant-categories")
product_router = APIRouter(prefix="/admin/merchant-products")
order_router = APIRouter(prefix="/admin/merchant-orders")

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ─────────────────────────── 商家分类 ───────────────────────────


@category_router.get("", response_model=list[MerchantCategoryItem], summary="商家分类列表")
async def category_list(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[MerchantCategoryItem]:
    current.require("merchant.read")
    return await service.list_categories(db)


@category_router.post("", response_model=MerchantCategoryItem, status_code=201, summary="新增商家分类")
async def category_create(
    body: MerchantCategoryCreate,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MerchantCategoryItem:
    current.require("merchant.manage")
    return await service.create_category(db, body)


@category_router.post("/reorder", status_code=204, summary="商家分类排序")
async def category_reorder(
    body: MerchantCategoryOrder,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    current.require("merchant.manage")
    await service.reorder_categories(db, body.ids)


@category_router.patch("/{category_id}", response_model=MerchantCategoryItem, summary="修改商家分类")
async def category_update(
    category_id: int = Path(..., ge=1),
    body: MerchantCategoryUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MerchantCategoryItem:
    current.require("merchant.manage")
    return await service.update_category(db, category_id, body)


@category_router.delete("/{category_id}", status_code=204, summary="删除商家分类")
async def category_delete(
    category_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    current.require("merchant.manage")
    await service.delete_category(db, category_id)


# ─────────────────────────── 商家 ───────────────────────────


@router.get("", response_model=MerchantPage, summary="查询商家列表")
async def merchant_list(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    category_id: int | None = Query(None, ge=1),
    keyword: str | None = Query(None, max_length=128),
    visible: bool | None = Query(None),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MerchantPage:
    current.require("merchant.read")
    return await service.list_merchants(db, page, page_size, category_id, keyword, visible)


@router.post("", response_model=MerchantItem, status_code=201, summary="新增商家")
async def merchant_create(
    body: MerchantCreate,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MerchantItem:
    current.require("merchant.manage")
    return await service.create_merchant(db, body)


@router.get("/options", response_model=list[MerchantOption], summary="商家下拉字典")
async def merchant_options(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[MerchantOption]:
    current.require("merchant.read")
    page = await service.list_merchants(db, 1, 500, None, None, None)
    return [MerchantOption(id=m.id, label=m.name) for m in page.items]


@router.patch("/{merchant_id}/visible", response_model=MerchantItem, summary="商家展示开关")
async def merchant_visible(
    merchant_id: int = Path(..., ge=1),
    visible: bool = Query(...),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MerchantItem:
    current.require("merchant.manage")
    return await service.set_merchant_visible(db, merchant_id, visible)


@router.put("/{merchant_id}", response_model=MerchantItem, summary="修改商家")
async def merchant_update(
    merchant_id: int = Path(..., ge=1),
    body: MerchantUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MerchantItem:
    current.require("merchant.manage")
    return await service.update_merchant(db, merchant_id, body)


@router.delete("/{merchant_id}", status_code=204, summary="删除商家")
async def merchant_delete(
    merchant_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    current.require("merchant.manage")
    await service.delete_merchant(db, merchant_id)


@router.get("/{merchant_id}", response_model=MerchantItem, summary="商家详情")
async def merchant_detail(
    merchant_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MerchantItem:
    current.require("merchant.read")
    return await service._get_merchant(db, merchant_id)


# ─────────────────────────── 商品 ───────────────────────────


@product_router.get("", response_model=MerchantProductPage, summary="查询商品列表")
async def product_list(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    merchant_id: int | None = Query(None, ge=1),
    keyword: str | None = Query(None, max_length=128),
    status: int | None = Query(None, ge=1, le=2),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MerchantProductPage:
    current.require("merchant.read")
    return await service.list_products(db, page, page_size, merchant_id, keyword, status)


@product_router.post("", response_model=MerchantProductItem, status_code=201, summary="新增商品")
async def product_create(
    body: MerchantProductCreate,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MerchantProductItem:
    current.require("merchant.manage")
    return await service.create_product(db, body)


@product_router.patch("/{product_id}/status", response_model=MerchantProductItem, summary="商品上架/下架")
async def product_status(
    product_id: int = Path(..., ge=1),
    status: int = Query(..., ge=1, le=2),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MerchantProductItem:
    current.require("merchant.manage")
    return await service.set_product_status(db, product_id, status)


@product_router.put("/{product_id}", response_model=MerchantProductItem, summary="修改商品")
async def product_update(
    product_id: int = Path(..., ge=1),
    body: MerchantProductUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MerchantProductItem:
    current.require("merchant.manage")
    return await service.update_product(db, product_id, body)


@product_router.delete("/{product_id}", status_code=204, summary="删除商品")
async def product_delete(
    product_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    current.require("merchant.manage")
    await service.delete_product(db, product_id)


@product_router.get("/{product_id}", response_model=MerchantProductItem, summary="商品详情")
async def product_detail(
    product_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MerchantProductItem:
    current.require("merchant.read")
    return await service._get_product(db, product_id)


# ─────────────────────────── 订单 ───────────────────────────


@order_router.get("", response_model=MerchantOrderPage, summary="查询商家订单")
async def order_list(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    merchant_id: int | None = Query(None, ge=1),
    status: str | None = Query(None, pattern="^(pending|paid|used|cancelled)$"),
    verify_status: str | None = Query(None, pattern="^(pending|verified)$"),
    keyword: str | None = Query(None, max_length=128),
    order_no: str | None = Query(None, max_length=64),
    buyer_keyword: str | None = Query(None, max_length=64),
    verify_start: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    verify_end: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MerchantOrderPage:
    current.require("merchant.read")
    return await service.list_orders(
        db, page, page_size, merchant_id, status, verify_status, keyword, order_no, buyer_keyword,
        verify_start, verify_end,
    )


@order_router.get("/export", summary="导出商家订单 Excel")
async def order_export(
    merchant_id: int | None = Query(None, ge=1),
    status: str | None = Query(None, pattern="^(pending|paid|used|cancelled)$"),
    verify_status: str | None = Query(None, pattern="^(pending|verified)$"),
    keyword: str | None = Query(None, max_length=128),
    order_no: str | None = Query(None, max_length=64),
    buyer_keyword: str | None = Query(None, max_length=64),
    verify_start: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    verify_end: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    current.require("merchant.read")
    content = await service.build_order_export(
        db, merchant_id=merchant_id, status=status, verify_status=verify_status, keyword=keyword,
        order_no=order_no, buyer_keyword=buyer_keyword, verify_start=verify_start, verify_end=verify_end,
    )
    return Response(
        content=content,
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="merchant-orders.xlsx"'},
    )


@order_router.patch("/{order_id}", response_model=MerchantOrderItem, summary="变更订单状态/取消订单")
async def order_update(
    order_id: int = Path(..., ge=1),
    body: MerchantOrderStatusUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MerchantOrderItem:
    current.require("merchant.manage")
    return await service.update_order_status(db, order_id, body)


@order_router.get("/{order_id}", response_model=MerchantOrderItem, summary="订单详情")
async def order_detail(
    order_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MerchantOrderItem:
    current.require("merchant.read")
    row = (
        await db.execute(
            text(f"{service._ORDER_SELECT} WHERE o.id = :id"), {"id": order_id}
        )
    ).mappings().first()
    if not row:
        from fastapi import HTTPException

        raise HTTPException(404, detail="订单不存在")
    return service._order_item(row)
