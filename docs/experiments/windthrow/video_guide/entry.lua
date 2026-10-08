-- A `::: entry` div = [Para(Image)] + text blocks -> two minipages side by side, unbreakable.
function Div(d)
  if not d.classes:includes("entry") then return nil end
  local img, rest = nil, {}
  for _, b in ipairs(d.content) do
    if img == nil and b.t == "Para" and #b.content == 1 and b.content[1].t == "Image" then
      img = b.content[1].src
    else
      table.insert(rest, b)
    end
  end
  local text = pandoc.write(pandoc.Pandoc(rest), "latex")
  local left = img and ("\\includegraphics[width=\\linewidth]{" .. img .. "}") or ""
  local tex = "\\par\\vspace{8pt}\\noindent\\begin{minipage}[t]{0.36\\textwidth}\\vspace{0pt}" .. left ..
              "\\end{minipage}\\hfill\\begin{minipage}[t]{0.60\\textwidth}\\vspace{0pt}\\raggedright " ..
              text .. "\\end{minipage}\\par\\vspace{10pt}"
  return pandoc.RawBlock("latex", tex)
end
