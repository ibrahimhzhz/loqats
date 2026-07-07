"""
Security middleware for the ATS application.
Provides request logging, rate limiting awareness, and security headers.
"""
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class SecurityLoggingMiddleware(BaseHTTPMiddleware):
    """
    Logs all requests for audit trail and security monitoring.
    Essential for multi-tenant applications to track data access patterns.
    """
    
    async def dispatch(self, request: Request, call_next):
        # Log incoming request
        timestamp = datetime.now().isoformat()
        method = request.method
        path = request.url.path
        client_ip = request.client.host if request.client else "unknown"
        
        # Extract user info if available (from auth)
        user_id = getattr(request.state, "user_id", None)
        company_id = getattr(request.state, "company_id", None)
        
        logger.info(
            f"REQUEST | {timestamp} | {client_ip} | {method} {path} | "
            f"User: {user_id} | Company: {company_id}"
        )
        
        try:
            response = await call_next(request)
            
            # Log response status
            logger.info(
                f"RESPONSE | {timestamp} | {method} {path} | "
                f"Status: {response.status_code} | User: {user_id}"
            )
            
            return response
            
        except Exception as e:
            logger.error(
                f"ERROR | {timestamp} | {method} {path} | "
                f"Error: {str(e)} | User: {user_id}"
            )
            raise


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Adds security headers to all responses.
    """
    
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        
        # Add security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        # Remove server header safely
        try:
            del response.headers["server"]
        except KeyError:
            pass
        
        return response


def log_security_event(event_type: str, user_id: int, company_id: int, details: str):
    """
    Log security-relevant events for audit trail.
    
    Args:
        event_type: Type of event (e.g., "UNAUTHORIZED_ACCESS", "DATA_LEAK_ATTEMPT")
        user_id: User who triggered the event
        company_id: Company context
        details: Additional details about the event
    """
    timestamp = datetime.now().isoformat()
    logger.warning(
        f"SECURITY_EVENT | {timestamp} | Type: {event_type} | "
        f"User: {user_id} | Company: {company_id} | Details: {details}"
    )