function setPointer(target, pointer, value) {
  if (pointer === "/") return value;
  const tokens = pointer.slice(1).split("/").map((item) => item.replaceAll("~1", "/").replaceAll("~0", "~"));
  let current = target;
  tokens.slice(0, -1).forEach((token) => {
    if (!current[token] || typeof current[token] !== "object") current[token] = {};
    current = current[token];
  });
  current[tokens.at(-1)] = value;
  return target;
}

const MINI_STYLE_FIELDS = new Set([
  "width", "height", "padding", "borderRadius", "clip", "backgroundColor", "linearGradient",
  "justifyContent", "alignItems", "fontSize", "fontWeight", "fontColor", "maxLines",
  "textOverflow", "textAlign", "layoutWeight",
]);

function getPointer(document, pointer) {
  return pointer.slice(1).split("/").reduce((current, token) => {
    const key = token.replaceAll("~1", "/").replaceAll("~0", "~");
    if (!current || !(key in current)) throw new Error(`WEB_RENDER_UNRESOLVED_BINDING: ${pointer}`);
    return current[key];
  }, document);
}

function textValue(value, document) {
  if (typeof value === "string") return value;
  if (value && typeof value.path === "string") return `{{ \${${value.path}} }}`;
  throw new Error("WEB_RENDER_UNSUPPORTED_TEXT_VALUE");
}

function translatedStyle(styles = {}) {
  const output = Object.fromEntries(
    Object.entries(styles).filter(([field]) => MINI_STYLE_FIELDS.has(field)),
  );
  if (!output.linearGradient) return output;
  if (output.linearGradient.direction === "RightBottom") {
    output.linearGradient = { ...output.linearGradient, direction: "Bottom" };
    return output;
  }
  if (!["Bottom", "Right", "Top", "Left"].includes(output.linearGradient.direction)) {
    throw new Error(`WEB_RENDER_UNSUPPORTED_GRADIENT: ${output.linearGradient.direction}`);
  }
  return output;
}

function resolveCompactText(value, document) {
  const source = textValue(value, document);
  const exactBinding = source.match(/^\{\{ \$\{(\/[^}]*)\} \}\}$/);
  if (exactBinding) return source;
  const expression = source.match(/^\{\{\s*(.*?)\s*\}\}$/)?.[1];
  if (!expression || !expression.includes("${")) return source;
  return expression.split("+").map((part) => {
    const token = part.trim();
    const binding = token.match(/^\$\{(\/[^}]*)\}$/);
    if (binding) return String(getPointer(document, binding[1]));
    const literal = token.match(/^'([^']*)'$/) || token.match(/^\"([^\"]*)\"$/);
    if (literal) return literal[1];
    throw new Error(`WEB_RENDER_UNSUPPORTED_TEXT_EXPRESSION: ${source}`);
  }).join("");
}

function compatibleType(component) {
  if (["Column", "Row", "Text"].includes(component.component)) return component.component;
  if (component.component === "Stack") return "Column";
  if (component.component === "Progress") return "Text";
  if (component.component === "Image") return "Text";
  throw new Error(`WEB_RENDER_UNSUPPORTED_COMPONENT: ${component.component}`);
}

export function adaptCreateMyCardA2UI(rawMessages) {
  if (!Array.isArray(rawMessages) || rawMessages.length !== 3) {
    throw new Error("WEB_RENDER_INVALID_MESSAGE_COUNT");
  }
  const create = rawMessages.find((item) => item.createSurface)?.createSurface;
  const update = rawMessages.find((item) => item.updateComponents)?.updateComponents;
  const dataUpdate = rawMessages.find((item) => item.updateDataModel)?.updateDataModel;
  if (!create || !update || !dataUpdate) throw new Error("WEB_RENDER_MISSING_MESSAGE");
  const components = new Map((update.components ?? []).map((item) => [item.id, item]));
  const rootId = update.root;
  if (!rootId || !components.has(rootId)) throw new Error("WEB_RENDER_EXPECTS_ONE_ROOT");
  const document = setPointer({}, dataUpdate.path ?? "/", dataUpdate.value ?? {});
  const convert = (id, ancestry = new Set()) => {
    if (ancestry.has(id)) throw new Error("WEB_RENDER_COMPONENT_CYCLE");
    const component = components.get(id);
    if (!component) throw new Error(`WEB_RENDER_MISSING_COMPONENT: ${id}`);
    const type = compatibleType(component);
    const nextAncestry = new Set(ancestry).add(id);
    const output = { id: component.id, type, style: translatedStyle(component.styles) };
    if (type === "Text") {
      if (component.component === "Progress") {
        output.text = resolveCompactText(component.value, document);
      } else if (component.component === "Image") {
        output.text = "▣";
      } else {
        output.text = resolveCompactText(component.content, document);
      }
      return output;
    }
    output.children = (component.children ?? []).map((child) => convert(child, nextAncestry));
    if (component.itemMargin !== undefined) output.itemMargin = component.itemMargin;
    if (component.onClick?.[0]?.call) output.onClick = { call: component.onClick[0].call };
    return output;
  };
  const root = convert(rootId);
  return [
    { createSurface: { surfaceId: create.surfaceId } },
    { updateComponents: { surfaceId: update.surfaceId, components: [root] } },
    { updateDataModel: { surfaceId: dataUpdate.surfaceId, data: document } },
  ];
}
