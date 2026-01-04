"""Основной FastAPI сервер"""
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from pydantic import BaseModel
from typing import Optional
from loguru import logger
from contextlib import asynccontextmanager
import sys
import json

from config import settings
from database import init_db, get_session, SyncLog, ProductMapping
from bitrix24_client import Bitrix24Client
from onec_client import OneCClient
from ai_reports import AIReportsService
from sync_service import SyncService
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select


# Настройка логирования
logger.remove()
logger.add(sys.stderr, level=settings.log_level)
logger.add("logs/app.log", rotation="1 day", retention="30 days", level=settings.log_level)


# Модели данных
class DealWebhook(BaseModel):
    """Webhook от Bitrix24 при изменении сделки"""
    event: str
    data: dict
    auth: Optional[dict] = None


class AIReportRequest(BaseModel):
    """Запрос на генерацию ИИ-отчёта"""
    query: str


class ProductMappingCreate(BaseModel):
    """Создание маппинга товара"""
    bitrix24_product_id: str
    bitrix24_product_name: str
    onec_product_code: str
    onec_product_name: str


# Lifecycle management
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Инициализация и завершение приложения"""
    logger.info("Starting application...")
    
    await init_db()
    logger.info("Database initialized")
    
    sync_service = SyncService()
    await sync_service.start_scheduler()
    logger.info("Sync scheduler started")
    
    yield
    
    logger.info("Shutting down application...")
    await sync_service.stop_scheduler()


# Создание приложения
app = FastAPI(
    title="1C-Bitrix24 Integration Middleware",
    description="Middleware для интеграции 1С:Бухгалтерия (KZ) с Битрикс24",
    version="1.0.0",
    lifespan=lifespan
)


@app.get("/")
async def root():
    """Проверка работоспособности сервиса"""
    return {
        "status": "running",
        "service": "1C-Bitrix24 Integration Middleware",
        "version": "1.0.0"
    }


@app.post("/webhook/bitrix24/deal")
async def bitrix24_deal_webhook(
    webhook: DealWebhook,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session)
):
    """Webhook для обработки событий сделок из Bitrix24"""
    logger.info(f"Received Bitrix24 webhook: {webhook.event}")
    
    try:
        if webhook.event not in ["ONCRMDEALADD", "ONCRMDEALUPDATE"]:
            return {"status": "ignored", "message": "Not a deal event"}
        
        deal_id = webhook.data.get("FIELDS", {}).get("ID")
        if not deal_id:
            raise HTTPException(status_code=400, detail="Deal ID not found")
        
        background_tasks.add_task(process_deal_to_1c, deal_id, session)
        
        return {
            "status": "accepted",
            "message": f"Deal {deal_id} queued for processing"
        }
    
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def process_deal_to_1c(deal_id: str, session: AsyncSession):
    """Обработка сделки и отправка в 1С"""
    bitrix24 = Bitrix24Client()
    onec = OneCClient()
    
    try:
        logger.info(f"Processing deal {deal_id} for 1C")
        
        deal = await bitrix24.get_deal(deal_id)
        is_kaspi = deal.get("UF_KASPI_PAYMENT") == "1" or "kaspi" in deal.get("TITLE", "").lower()
        
        if not is_kaspi:
            logger.info(f"Deal {deal_id} is not a Kaspi payment, skipping")
            return
        
        products = await bitrix24.get_deal_products(deal_id)
        contact_id = deal.get("CONTACT_ID")
        contact = await bitrix24.get_contact(contact_id) if contact_id else {}
        
        customer_name = contact.get("NAME", "") + " " + contact.get("LAST_NAME", "")
        customer_phone = contact.get("PHONE", [{}])[0].get("VALUE", "") if contact.get("PHONE") else ""
        
        mapped_products = []
        for product in products:
            stmt = select(ProductMapping).where(
                ProductMapping.bitrix24_product_id == str(product.get("PRODUCT_ID"))
            )
            result = await session.execute(stmt)
            mapping = result.scalar_one_or_none()
            
            if mapping:
                mapped_products.append({
                    "code": mapping.onec_product_code,
                    "name": mapping.onec_product_name,
                    "quantity": int(product.get("QUANTITY", 1)),
                    "price": float(product.get("PRICE", 0))
                })
        
        if not mapped_products:
            logger.error(f"No mapped products for deal {deal_id}")
            return
        
        order_data = {
            "deal_id": deal_id,
            "customer": {
                "name": customer_name.strip() or "Клиент Kaspi",
                "phone": customer_phone
            },
            "products": mapped_products,
            "total_amount": float(deal.get("OPPORTUNITY", 0)),
            "payment_type": "Kaspi"
        }
        
        result = await onec.create_order(order_data)
        
        if result.get("success"):
            order_number = result.get("order_number")
            await bitrix24.update_deal_field(deal_id, "UF_1C_ORDER_ID", order_number)
            await bitrix24.create_activity(
                deal_id,
                "Накладная создана в 1С",
                f"Номер накладной: {order_number}"
            )
            
            log_entry = SyncLog(
                sync_type="order_to_1c",
                direction="bitrix24_to_1c",
                status="success",
                entity_id=deal_id,
                request_data=json.dumps(order_data),
                response_data=json.dumps(result)
            )
            session.add(log_entry)
            await session.commit()
            
            logger.info(f"Order {order_number} created in 1C for deal {deal_id}")
    
    except Exception as e:
        logger.error(f"Error processing deal {deal_id}: {e}")
    
    finally:
        await bitrix24.close()
        await onec.close()


@app.post("/api/ai-report")
async def generate_ai_report(request: AIReportRequest):
    """Генерация аналитического отчёта через ИИ"""
    ai_service = AIReportsService()
    
    try:
        logger.info(f"Generating AI report for query: {request.query}")
        report = await ai_service.generate_report(request.query)
        return {"report": report}
    
    except Exception as e:
        logger.error(f"Error generating AI report: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
    finally:
        await ai_service.close()


@app.post("/api/sync/stock")
async def trigger_stock_sync(background_tasks: BackgroundTasks):
    """Ручной запуск синхронизации остатков"""
    sync_service = SyncService()
    background_tasks.add_task(sync_service.sync_stock_to_bitrix24)
    
    return {
        "status": "started",
        "message": "Stock synchronization started"
    }


@app.post("/api/mapping/product")
async def create_product_mapping(
    mapping: ProductMappingCreate,
    session: AsyncSession = Depends(get_session)
):
    """Создать маппинг товара"""
    try:
        new_mapping = ProductMapping(
            bitrix24_product_id=mapping.bitrix24_product_id,
            bitrix24_product_name=mapping.bitrix24_product_name,
            onec_product_code=mapping.onec_product_code,
            onec_product_name=mapping.onec_product_name
        )
        
        session.add(new_mapping)
        await session.commit()
        
        logger.info(f"Created product mapping: {mapping.bitrix24_product_id} -> {mapping.onec_product_code}")
        
        return {
            "status": "success",
            "message": "Product mapping created"
        }
    
    except Exception as e:
        logger.error(f"Error creating product mapping: {e}")
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/mapping/products")
async def get_product_mappings(session: AsyncSession = Depends(get_session)):
    """Получить все маппинги товаров"""
    stmt = select(ProductMapping)
    result = await session.execute(stmt)
    mappings = result.scalars().all()
    
    return {
        "mappings": [
            {
                "id": m.id,
                "bitrix24_product_id": m.bitrix24_product_id,
                "bitrix24_product_name": m.bitrix24_product_name,
                "onec_product_code": m.onec_product_code,
                "onec_product_name": m.onec_product_name
            }
            for m in mappings
        ]
    }
