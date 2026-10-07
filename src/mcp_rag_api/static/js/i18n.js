/* Idioma da interface (pt-BR / en / es) — dicionários em static/i18n/<idioma>.js. */
/* ---------------------------------------------------------------- idioma (pt-BR / en / es) */

// dicionários em static/i18n/<idioma>.js; chave ausente cai no pt-BR e, por fim, na própria chave.
// A escolha fica no navegador; sem escolha salva, segue o idioma do navegador (o <head> aplica).
const LANG_KEY = "concilium:lang";
const LANGS = [
  { id: "pt-BR", label: "PT", name: "Português" },
  { id: "en", label: "EN", name: "English" },
  { id: "es", label: "ES", name: "Español" },
];
// locale do Intl (datas/números) e do editor Toast UI para cada idioma da interface
const LOCALES = { "pt-BR": "pt-BR", en: "en-US", es: "es-ES" };

function currentLang() {
  const lang = document.documentElement.lang;
  return LANGS.some((l) => l.id === lang) ? lang : "pt-BR";
}

const locale = () => LOCALES[currentLang()];

function setLang(lang) {
  document.documentElement.lang = lang;
  try {
    localStorage.setItem(LANG_KEY, JSON.stringify(lang));
  } catch {
    /* sem armazenamento local: vale só nesta aba */
  }
}

// t("keys.counts", { active: 2 }): {nome} vira o valor; mensagem { one, other } escolhe a forma por vars.count.
// Não escapa nada: quem interpola dado do usuário passa o valor já com escHtml.
function t(key, vars = {}) {
  const messages = window.I18N || {};
  let msg = messages[currentLang()]?.[key] ?? messages["pt-BR"]?.[key] ?? key;
  if (typeof msg === "object") msg = msg[new Intl.PluralRules(locale()).select(vars.count ?? 0)] ?? msg.other;
  return msg.replace(/\{(\w+)\}/g, (m, name) => (name in vars ? String(vars[name]) : m));
}

export { LANGS, currentLang, locale, setLang, t };
