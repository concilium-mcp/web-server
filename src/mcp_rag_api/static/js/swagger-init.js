/* Inicialização do Swagger UI em /dash/docs. Fica num arquivo (não em <script> inline) porque a CSP da
   dashboard é script-src 'self'; mesmos parâmetros padrão do get_swagger_ui_html do FastAPI. */
window.ui = SwaggerUIBundle({
  url: "/dash/api/openapi.json",
  dom_id: "#swagger-ui",
  layout: "BaseLayout",
  deepLinking: true,
  showExtensions: true,
  showCommonExtensions: true,
  presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
});
