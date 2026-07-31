import os
import base64
import logging
from flask import render_template, make_response, current_app
from xhtml2pdf import pisa
from io import BytesIO

from app.extensions import limiter
from app.utils import get_student_data_from_db
from app.errors import api_error
from app.exports import exports_bp as exports

logger = logging.getLogger(__name__)

_logo_uri_cache = None


def _mask_cedula(identification: str) -> str:
    """Enmascara la cédula en logs (PII): V-12345678 -> V-*****678."""
    if len(identification) <= 5:
        return '***'
    return f"{identification[:2]}{'*' * (len(identification) - 5)}{identification[-3:]}"


def _get_logo_uri() -> str:
    global _logo_uri_cache
    if _logo_uri_cache is None:
        # root_path apunta a la carpeta "app", por lo que subimos un nivel para llegar a "static"
        logo_path = os.path.abspath(os.path.join(current_app.root_path, '..', 'static', 'img', 'logo-una.png'))
        with open(logo_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
        _logo_uri_cache = f'data:image/png;base64,{encoded_string}'
    return _logo_uri_cache


# Endpoint público por decisión de producto: el rate limit es la mitigación
# frente a enumeración de cédulas y agotamiento de workers por render de PDF.
@exports.route("/exports/pdf/<string:identification>/<int:period_id>", methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def exports_student_pdf(identification, period_id):
    masked = _mask_cedula(identification)
    try:
        logger.info("Generando PDF de ficha académica: cédula=%s período=%d", masked, period_id)
        student_data = get_student_data_from_db(identification)

        if not student_data:
            logger.warning("PDF no generado: estudiante %s no encontrado", masked)
            return api_error("ESTUDIANTE_NO_ENCONTRADO", "Estudiante no encontrado", http_status=404)

        # Filtrar periodos para mostrar solo el solicitado
        if 'periodos' in student_data:
            student_data['periodos'] = [p for p in student_data['periodos'] if p['id'] == period_id]

        # Agregar fecha de emisión
        from datetime import datetime
        student_data['fecha_emision'] = datetime.now().strftime("%d/%m/%Y")

        html_content = render_template(
            'academic-record.html', data=student_data, period_id=period_id, logo_uri=_get_logo_uri()
        )

        pdf = BytesIO()
        pisa_status = pisa.CreatePDF(BytesIO(html_content.encode('utf-8')), dest=pdf)

        if pisa_status.err:
            logger.error("xhtml2pdf falló generando PDF para cédula=%s período=%d", masked, period_id)
            return api_error("ERROR_PDF", "No se pudo generar el PDF.", http_status=500)

        response = make_response(pdf.getvalue())
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'inline; filename=ficha_{identification}_{period_id}.pdf'
        return response

    except Exception:
        logger.exception("Error generando PDF para cédula=%s período=%d", masked, period_id)
        return api_error("ERROR_INTERNO", "Error interno al generar el PDF.", http_status=500)
