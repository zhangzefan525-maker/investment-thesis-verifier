// 后端会在结论文案里用 **粗体** 标注关键词。这里做最小化渲染：
// 切分文本节点并包 <strong>，不引入 markdown 依赖，也不设置 innerHTML，
// 因此不存在 HTML 注入面。
export default function RichText({ text, className = '', id }) {
  const parts = String(text ?? '').split('**')
  return (
    <span className={className} id={id}>
      {parts.map((p, i) =>
        i % 2 === 1 ? (
          <strong key={i} className="font-semibold text-ink-900">
            {p}
          </strong>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </span>
  )
}
