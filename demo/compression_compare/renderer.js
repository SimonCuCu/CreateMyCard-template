const SUPPORTED_TYPES = new Set(["Text", "Row", "Column"]);
const STYLE_FIELDS = new Set([
  "width", "height", "padding", "borderRadius", "clip", "backgroundColor", "linearGradient",
  "justifyContent", "alignItems", "fontSize", "fontWeight", "fontColor", "maxLines",
  "textOverflow", "textAlign", "layoutWeight",
]);

function argbToCss(color) {
  if (!color || !color.startsWith("#")) return color;
  if (color.length === 7) return color;
  if (color.length !== 9) throw new Error(`WEB_RENDER_INVALID_COLOR: ${color}`);
  const alpha = parseInt(color.slice(1, 3), 16) / 255;
  const red = parseInt(color.slice(3, 5), 16);
  const green = parseInt(color.slice(5, 7), 16);
  const blue = parseInt(color.slice(7, 9), 16);
  return `rgba(${red}, ${green}, ${blue}, ${alpha.toFixed(3)})`;
}

function resolvePointer(document, pointer) {
  if (!pointer.startsWith("/")) throw new Error(`WEB_RENDER_INVALID_BINDING: ${pointer}`);
  return pointer.slice(1).split("/").reduce((current, token) => {
    const key = token.replaceAll("~1", "/").replaceAll("~0", "~");
    if (current === null || current === undefined || !(key in current)) {
      throw new Error(`WEB_RENDER_UNRESOLVED_BINDING: ${pointer}`);
    }
    return current[key];
  }, document);
}

function resolveValue(value, data) {
  if (typeof value !== "string") return value;
  const match = value.match(/^\{\{ \$\{(\/[^}]*)\} \}\}$/);
  return match ? String(resolvePointer(data, match[1])) : value;
}

function dimension(value) {
  if (value === "matchParent") return "100%";
  return typeof value === "number" ? `${value}px` : value;
}

function applyGradient(element, gradient) {
  const directions = { Bottom: "to bottom", Right: "to right", Top: "to top", Left: "to left" };
  if (!(gradient.direction in directions)) throw new Error(`WEB_RENDER_UNSUPPORTED_GRADIENT: ${gradient.direction}`);
  const stops = gradient.colors.map(([color, position]) => `${argbToCss(color)} ${position * 100}%`);
  element.style.background = `linear-gradient(${directions[gradient.direction]}, ${stops.join(", ")})`;
}

function applyStyle(element, style = {}) {
  Object.keys(style).forEach((field) => {
    if (!STYLE_FIELDS.has(field)) throw new Error(`WEB_RENDER_UNSUPPORTED_FIELD: style.${field}`);
  });
  if (style.width !== undefined) element.style.width = dimension(style.width);
  if (style.height !== undefined) element.style.height = dimension(style.height);
  if (style.padding !== undefined) element.style.padding = dimension(style.padding);
  if (style.borderRadius !== undefined) element.style.borderRadius = dimension(style.borderRadius);
  if (style.clip) element.style.overflow = "hidden";
  if (style.backgroundColor) element.style.backgroundColor = argbToCss(style.backgroundColor);
  if (style.linearGradient) applyGradient(element, style.linearGradient);
  if (style.justifyContent) {
    const justifyValues = { spaceBetween: "space-between", spaceAround: "space-around", spaceEvenly: "space-evenly" };
    element.style.justifyContent = justifyValues[style.justifyContent] || style.justifyContent;
  }
  if (style.alignItems) element.style.alignItems = style.alignItems;
  if (style.fontSize !== undefined) element.style.fontSize = dimension(style.fontSize);
  if (style.fontWeight !== undefined) element.style.fontWeight = style.fontWeight;
  if (style.fontColor) element.style.color = argbToCss(style.fontColor);
  if (style.textAlign) element.style.textAlign = style.textAlign;
  if (style.layoutWeight !== undefined) element.style.flex = `${style.layoutWeight} 1 0`;
  if (style.maxLines !== undefined) {
    element.style.display = "-webkit-box";
    element.style.webkitLineClamp = style.maxLines;
    element.style.webkitBoxOrient = "vertical";
    element.style.overflow = "hidden";
  }
  if (style.textOverflow === "ellipsis") element.style.textOverflow = "ellipsis";
}

function assertFields(component) {
  const common = new Set(["id", "type", "style"]);
  const byType = {
    Text: new Set([...common, "text"]),
    Row: new Set([...common, "children", "itemMargin"]),
    Column: new Set([...common, "children", "itemMargin", "onClick"]),
  };
  Object.keys(component).forEach((field) => {
    if (!byType[component.type].has(field)) throw new Error(`WEB_RENDER_UNSUPPORTED_FIELD: ${component.type}.${field}`);
  });
}

function buildComponent(component, data) {
  if (!SUPPORTED_TYPES.has(component.type)) throw new Error(`WEB_RENDER_UNSUPPORTED_COMPONENT: ${component.type}`);
  assertFields(component);
  const element = document.createElement(component.type === "Text" ? "div" : "section");
  element.dataset.componentId = component.id;
  element.dataset.componentType = component.type;
  element.className = `a2ui-${component.type.toLowerCase()}`;
  applyStyle(element, component.style);
  if (component.type === "Text") {
    element.textContent = resolveValue(component.text, data);
    return element;
  }
  if (component.itemMargin !== undefined) element.style.gap = dimension(component.itemMargin);
  (component.children || []).forEach((child) => element.append(buildComponent(child, data)));
  if (component.onClick) {
    element.dataset.action = component.onClick.call;
    element.tabIndex = 0;
  }
  return element;
}

export function renderA2UI(messages, target) {
  if (!Array.isArray(messages) || messages.length !== 3) throw new Error("WEB_RENDER_INVALID_MESSAGE_COUNT");
  const create = messages.find((message) => message.createSurface)?.createSurface;
  const update = messages.find((message) => message.updateComponents)?.updateComponents;
  const data = messages.find((message) => message.updateDataModel)?.updateDataModel;
  if (!create || !update || !data) throw new Error("WEB_RENDER_MISSING_MESSAGE");
  if (create.surfaceId !== update.surfaceId || create.surfaceId !== data.surfaceId) {
    throw new Error("WEB_RENDER_SURFACE_ID_MISMATCH");
  }
  if (!Array.isArray(update.components) || update.components.length !== 1) {
    throw new Error("WEB_RENDER_EXPECTS_ONE_ROOT");
  }
  target.replaceChildren(buildComponent(update.components[0], data.data));
  return { surfaceId: create.surfaceId, rootId: update.components[0].id };
}

window.renderA2UI = (messages) => renderA2UI(messages, document.getElementById("widget-canvas"));
