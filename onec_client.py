"""Клиент для работы с 1С HTTP-сервисом"""
import httpx
from typing import Dict, List
from loguru import logger
from config import settings
import base64


class OneCClient:
    """Клиент для взаимодействия с 1С через HTTP-сервис"""
    
    def __init__(self):
        self.base_url = settings.onec_base_url.rstrip('/')
        self.service_path = settings.onec_http_service_path
        self.username = settings.onec_username
        self.password = settings.onec_password
        
        # Создаём базовую авторизацию
        auth_string = f"{self.username}:{self.password}"
        auth_bytes = auth_string.encode('utf-8')
        auth_b64 = base64.b64encode(auth_bytes).decode('utf-8')
        
        self.client = httpx.AsyncClient(
            timeout=60.0,
            headers={
                "Authorization": f"Basic {auth_b64}",
                "Content-Type": "application/json"
            }
        )
    
    async def _call_service(self, operation: str, data: Dict) -> Dict:
        """Вызов операции HTTP-сервиса 1С"""
        url = f"{self.base_url}{self.service_path}/{operation}"
        
        try:
            logger.info(f"Calling 1C service: {operation}")
            logger.debug(f"Request data: {data}")
            
            response = await self.client.post(url, json=data)
            response.raise_for_status()
            
            result = response.json()
            logger.debug(f"Response: {result}")
            
            return result
        except httpx.HTTPError as e:
            logger.error(f"HTTP error calling 1C: {e}")
            raise
        except Exception as e:
            logger.error(f"Error calling 1C service: {e}")
            raise
    
    async def create_order(self, order_data: Dict) -> Dict:
        """Создать накладную в 1С"""
        logger.info(f"Creating order in 1C for deal {order_data.get('deal_id')}")
        return await self._call_service("create_order", order_data)
    
    async def get_stock_balances(self, warehouse: str = None) -> List[Dict]:
        """Получить остатки товаров со склада"""
        logger.info(f"Getting stock balances from 1C (warehouse: {warehouse or 'all'})")
        params = {"warehouse": warehouse} if warehouse else {}
        result = await self._call_service("get_stock_balances", params)
        return result.get("balances", [])
    
    async def close(self):
        """Закрыть HTTP клиент"""
        await self.client.aclose()
