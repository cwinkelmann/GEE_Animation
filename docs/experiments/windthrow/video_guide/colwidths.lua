-- Give every table relative column widths proportional to its content, so LaTeX wraps cells
-- instead of running past the margin. Width share = clamp(max cell length) per column.
local function cell_len(cell)
  local s = pandoc.utils.stringify(cell.contents or cell)
  local longest = 0
  for word in s:gmatch("%S+") do if #word > longest then longest = #word end end
  return #s, longest
end
function Table(tbl)
  local ncol = #tbl.colspecs
  local maxlen, minword = {}, {}
  for i = 1, ncol do maxlen[i] = 1; minword[i] = 1 end
  local function scan(rows)
    for _, row in ipairs(rows) do
      for i, cell in ipairs(row.cells) do
        local l, w = cell_len(cell)
        if l > maxlen[i] then maxlen[i] = l end
        if w > minword[i] then minword[i] = w end
      end
    end
  end
  scan(tbl.head.rows)
  for _, body in ipairs(tbl.bodies) do scan(body.body) end
  local share, total = {}, 0
  for i = 1, ncol do
    -- cap very long cells so one prose column cannot starve the others; never below the longest word
    share[i] = math.max(math.min(maxlen[i], 60), minword[i] + 2, 6)
    total = total + share[i]
  end
  for i = 1, ncol do
    tbl.colspecs[i] = { tbl.colspecs[i][1], 0.98 * share[i] / total }
  end
  return tbl
end
