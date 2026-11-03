"use strict";
(() => {
  const boot = JSON.parse(document.getElementById("boot").textContent);
  let data = boot.report;
  let fileIndex = 0, entityKey = null, tab = "properties", selectedProperty = null;
  let query = "", statusFilter = "all", showUnchanged = false, mode = "git", rawMode = "diff";
  let decisions = data?.review?.decisions || {};
  let mergeHistory = [], merging = false, mergeError = "";
  const $ = id => document.getElementById(id);
  const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[char]));
  const labels = {unchanged:"Unchanged", left:"A changed", right:"B changed", both:"Both agree", combined:"Independent edits", conflict:"Conflict", added:"Added", removed:"Removed", modified:"Modified"};
  const kindLabels = {node:"Node", sub_resource:"Embedded resource", ext_resource:"External resource", gd_scene:"Scene metadata", gd_resource:"Resource metadata", connection:"Signal connection", resource:"Resource", order:"Child ordering", editable:"Editable instance"};
  const badge = (status, label) => `<span class="badge ${escape(status)}">${escape(label || labels[status] || status)}</span>`;
  const file = () => data?.files[fileIndex];
  const entity = () => file()?.entities.find(item => item.key === entityKey);
  const decisionKey = (f, e, p) => JSON.stringify([f.path, e?.key || "$file", p]);
  const empty = (title, message, icon="◇") => `<div class="empty"><span class="empty-icon">${icon}</span><strong>${escape(title)}</strong><p>${escape(message)}</p></div>`;
  const concise = value => value === null ? "Absent / no override" : value.length > 110 ? value.slice(0, 107) + "…" : value;

  function toast(message) {
    $("toast").textContent = message;
    $("toast").hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => $("toast").hidden = true, 3300);
  }
  function changedLabel(item) {
    if (data.three_way && !item.exists[0] && (item.exists[1] || item.exists[2])) return item.status === "conflict" ? "conflict" : "added";
    if (data.three_way && item.exists[0] && (!item.exists[1] || !item.exists[2])) return item.status === "conflict" ? "conflict" : "removed";
    return item.status;
  }
  function conflicts() {
    const list = [];
    for (const f of data?.files || []) {
      if (!f.entities.length && f.status === "conflict") list.push({f, e:null, prop:"$file", key:decisionKey(f,null,"$file")});
      for (const e of f.entities) {
        if (e.structural_conflict) list.push({f,e,prop:"$structure",key:decisionKey(f,e,"$structure")});
        for (const row of e.rows) if (row.status === "conflict") list.push({f,e,prop:row.property,key:decisionKey(f,e,row.property)});
      }
    }
    return list;
  }
  function selectFile(index) {
    fileIndex = index;
    const f = file();
    entityKey = f?.entities.find(e => e.status === "conflict")?.key || f?.entities.find(e => e.status !== "unchanged")?.key || f?.entities.find(e => e.kind === "node")?.key || f?.entities[0]?.key || null;
    selectedProperty = null;
    tab = f?.entities.length ? "properties" : "raw";
    render();
  }
  function selectEntity(key) {
    entityKey = key; selectedProperty = null; tab = "properties"; render();
  }
  // Outline filter: every status maps to one of these groups.
  const filters = [["all","All"],["added","Added"],["modified","Modified"],["removed","Removed"],["conflict","Conflict"],["unchanged","Unchanged"]];
  const category = item => { const s = changedLabel(item); return ["added","removed","conflict","unchanged"].includes(s) ? s : "modified"; };
  const matchesFilter = item => statusFilter === "all" || category(item) === statusFilter;
  const matchesQuery = e => !query || (e.label + " " + e.path).toLowerCase().includes(query);
  function visibleEntity(e) {
    return matchesFilter(e) && matchesQuery(e);
  }
  function renderFilter() {
    const entities = file()?.entities || [];
    const count = key => key === "all" ? entities.length : entities.filter(e => category(e) === key).length;
    $("status-filter").innerHTML = filters.map(([key,label]) => `<button type="button" data-filter="${key}" class="${statusFilter === key ? "active" : ""}" aria-pressed="${statusFilter === key}">${key === "all" ? "" : `<i class="dot ${key}"></i>`}${label}<small>${count(key)}</small></button>`).join("");
    $("status-filter").querySelectorAll("[data-filter]").forEach(button => button.onclick = () => { statusFilter = button.dataset.filter; selectedProperty = null; render(); });
  }
  // Map resource key -> key of the node it belongs to: its only user, or the nearest node
  // all its users are inside. Resources shared across the scene stay under RESOURCES.
  function resourceHomes(entities) {
    const nodes = new Set(entities.filter(e => e.kind === "node").map(e => e.key));
    const homes = new Map();
    for (const e of entities) {
      if (!["sub_resource","ext_resource"].includes(e.kind)) continue;
      const owners = [...new Set((e.owners || []).flat())];
      if (!owners.length || owners.includes("resource")) continue;
      let home = owners[0];
      for (const other of owners.slice(1)) {
        const a = home === "." ? [] : home.split("/"), b = other === "." ? [] : other.split("/");
        let n = 0; while (n < a.length && n < b.length && a[n] === b[n]) n++;
        home = n ? a.slice(0, n).join("/") : ".";
      }
      if (home === "." && owners.length > 1) continue;
      if (nodes.has("node:" + home)) homes.set(e.key, "node:" + home);
    }
    return homes;
  }
  function renderSidebar() {
    renderFilter();
    $("file-count").textContent = data.files.length;
    $("file-list").innerHTML = data.files.map((f,i) => {
      if (statusFilter !== "all" && category(f) !== statusFilter && !f.entities.some(matchesFilter)) return "";
      if (query && !f.path.toLowerCase().includes(query) && !f.entities.some(visibleEntity)) return "";
      const name = f.path.split("/").pop();
      const icon = f.kind === "scene" ? "▧" : f.kind === "resource" ? "◇" : f.kind === "binary" ? "◩" : "{ }";
      return `<button class="file-row ${i === fileIndex ? "active" : ""}" data-file="${i}" title="${escape(f.path)}" aria-pressed="${i === fileIndex}"><span class="file-icon">${icon}</span><span class="filename">${escape(name)}</span>${badge(changedLabel(f))}</button>`;
    }).join("") || `<p class="small muted">No files match this filter.</p>`;
    $("file-list").querySelectorAll("[data-file]").forEach(button => button.onclick = () => selectFile(Number(button.dataset.file)));
    const entities = file()?.entities || [];
    $("entity-count").textContent = entities.filter(e => e.kind === "node").length;
    // Textures, shaders, materials and meshes are listed under the node that uses them.
    const homes = resourceHomes(entities);
    const nested = new Map();
    for (const [key, home] of homes) (nested.get(home) || nested.set(home, []).get(home)).push(entities.find(e => e.key === key));
    const groups = [
      ["", entities.filter(e => e.kind === "node")],
      ["RESOURCES", entities.filter(e => ["sub_resource","ext_resource","resource"].includes(e.kind) && !homes.has(e.key))],
      ["STRUCTURE & SIGNALS", entities.filter(e => !["node","sub_resource","ext_resource","resource"].includes(e.kind))]
    ];
    const row = (e, indent, visible, extra = "") => {
      const name = e.kind === "node" ? e.label.split("/").pop() : e.label;
      return `<button class="tree-row ${extra} ${e.key === entityKey ? "active" : ""} ${visible ? "" : "context"}" data-entity="${escape(e.key)}" style="padding-left:${10+indent}px" title="${escape(e.label)}" aria-pressed="${e.key === entityKey}"><span class="tree-icon">${e.kind === "node" ? "◇" : e.kind === "connection" ? "ϟ" : "▧"}</span><span class="tree-name">${escape(name)}</span><i class="dot ${changedLabel(e)}" aria-label="${escape(labels[e.status])}"></i></button>`;
    };
    $("scene-tree").innerHTML = groups.map(([title,items]) => {
      const shownNested = title ? [] : [...nested.values()].flat().filter(visibleEntity);
      const shown = items.filter(visibleEntity);
      if (!shown.length && !shownNested.length) return "";
      const context = new Set();
      const addContext = path => {
        const parts = path === "." ? [] : path.split("/");
        context.add(".");
        for (let i = 1; i <= parts.length; i++) context.add(parts.slice(0, i).join("/"));
      };
      if (!title) {
        if (statusFilter !== "all") for (const e of shown) addContext(e.path === "." ? "." : e.path.split("/").slice(0, -1).join("/") || ".");
        for (const r of shownNested) { const home = entities.find(e => e.key === homes.get(r.key)); if (home) addContext(home.path); }
      }
      const visible = new Set(shown);
      items = items.filter(e => visible.has(e) || (e.kind === "node" && context.has(e.path)));
      return (title ? `<div class="tree-group">${title}</div>` : "") + items.map(e => {
        const indent = e.kind === "node" && e.path !== "." ? Math.min(e.path.split("/").length, 5)*13 : 0;
        const children = (nested.get(e.key) || []).filter(visibleEntity);
        return row(e, indent, visible.has(e)) + children.map(r => row(r, indent + 22, true, "nested")).join("");
      }).join("");
    }).join("") || `<p class="small muted" style="padding:10px">${entities.length ? "No entities match this filter." : "Open a scene or resource to explore its structure."}</p>`;
    $("scene-tree").querySelectorAll("[data-entity]").forEach(button => button.onclick = () => selectEntity(button.dataset.entity));
  }
  function displayValue(value) {
    if (value === null) return '<span class="absent">Absent / no override</span>';
    let swatch = "";
    const color = value.match(/^Color\(\s*([\d.eE+-]+),\s*([\d.eE+-]+),\s*([\d.eE+-]+),\s*([\d.eE+-]+)\s*\)$/);
    if (color) {
      const components = color.slice(1,4).map(v => Math.max(0,Math.min(255,Math.round(Number(v)*255))));
      const alpha = Math.max(0,Math.min(1,Number(color[4])));
      if (components.every(Number.isFinite) && Number.isFinite(alpha)) swatch = `<span class="value-color" style="background:rgba(${components.join(",")},${alpha})"></span>`;
    }
    return `${swatch}<span class="value">${escape(value)}</span>`;
  }
  function renderProperties() {
    const e = entity();
    if (!e) return empty("Choose a node or resource", "Select an item in the scene outline to inspect its properties.");
    const rows = e.rows.filter(row => statusFilter === "conflict" ? row.status === "conflict" : statusFilter === "unchanged" ? row.status === "unchanged" : showUnchanged || row.status !== "unchanged");
    if (!selectedProperty) selectedProperty = e.structural_conflict ? "$structure" : rows.find(r => r.status === "conflict")?.property || rows[0]?.property;
    let html = e.structural_conflict ? `<div class="inline-note">Structural conflict: ${escape(e.structural_conflict)}. Review the node or resource as a whole, including its dependencies.</div>` : "";
    if (!rows.length) return html + empty("No matching property changes", showUnchanged || statusFilter !== "all" ? "Try another filter above the outline." : "Enable “Show unchanged” to inspect every serialized property.", "✓");
    const cols = data.three_way ? [0,1,2] : [1,2];
    html += `<table class="properties"><thead><tr><th>PROPERTY</th>${cols.map(c => `<th>${["BASE","A · LEFT","B · RIGHT"][c]}</th>`).join("")}</tr></thead><tbody>`;
    html += rows.map(row => `<tr tabindex="0" role="button" aria-label="Review ${escape(row.property)}" class="${selectedProperty === row.property ? "selected" : ""}" data-property="${escape(row.property)}"><td class="property-name">${escape(row.property.replace(/^@/,""))}${decisions[decisionKey(file(),e,row.property)] ? ' <span title="Decision recorded" style="color:var(--purple)">✓</span>' : ""}</td>${cols.map(c => {
      const changed = c > 0 && row.status !== "unchanged" && (data.three_way ? row.values[c] !== row.values[0] : c === 2);
      return `<td class="${changed ? row.status === "conflict" ? "clash" : "changed" : ""}">${displayValue(row.values[c])}</td>`;
    }).join("")}</tr>`).join("") + "</tbody></table>";
    return html;
  }
  function renderNodeMatches() {
    const f = file(), container = $("node-matches");
    const suggestions = f?.match_suggestions || [], confirmed = f?.node_matches || [], options = f?.match_options || [];
    container.hidden = !suggestions.length && !confirmed.length && !options.length;
    if (container.hidden) { container.innerHTML = ""; return; }
    const sourceLabel = data.three_way ? "Base" : "A";
    container.innerHTML = `<details ${suggestions.length ? "open" : ""}><summary>Node identity · ${confirmed.length} confirmed · ${suggestions.length} suggestions</summary><p class="small muted">A rename or parent change can look like a deletion and addition. Confirm a match to compare the same node across versions. Matching a parent also matches compatible children at unchanged relative paths. Property decisions for this file will be cleared.</p>${boot.live && suggestions.filter(m => !m.ambiguous).length > 1 ? `<div class="match-bulk"><button id="match-all-clear" class="primary">Match all ${suggestions.filter(m => !m.ambiguous).length} clear suggestions</button><small class="muted">Each is the best match for both of its nodes by a clear margin. Review the list first; Undo match works for each.</small></div>` : ""}${!boot.live ? '<p class="inline-note">This saved report is read-only for node matching. Run SceneDiff locally to confirm or undo matches.</p>' : ""}${confirmed.map((match,i) => `<div class="match-row"><div><b>${escape(sourceLabel + ": " + match.base_path)} → ${escape((match.side === "left" ? "A" : "B") + ": " + match.target_path)}</b><small>Confirmed identity · source files unchanged</small></div><button data-undo-match="${i}" ${boot.live ? "" : "disabled"}>Undo match</button></div>`).join("")}${suggestions.map((match,i) => `<div class="match-row"><div><b>${escape(sourceLabel + ": " + match.base_path)} → ${escape((match.side === "left" ? "A" : "B") + ": " + match.target_path)}</b><small>${escape(match.reasons.join(" · "))}</small>${match.ambiguous ? '<small class="match-ambiguous">Multiple plausible matches — inspect the nodes before choosing.</small>' : '<small class="match-clear">Clear best match · similarity is a suggestion, not proof of identity.</small>'}</div><button data-confirm-match="${i}" ${boot.live ? "" : "disabled"}>Match nodes</button></div>`).join("")}</details>`;
    if ($("match-all-clear")) $("match-all-clear").onclick = () =>
      rematch([...confirmed, ...suggestions.filter(m => !m.ambiguous).map(m => ({side:m.side, base_path:m.base_path, target_path:m.target_path}))]);
    container.querySelectorAll("[data-confirm-match]").forEach(button => button.onclick = () => {
      const match = suggestions[Number(button.dataset.confirmMatch)];
      rematch([...confirmed,{side:match.side,base_path:match.base_path,target_path:match.target_path}]);
    });
    container.querySelectorAll("[data-undo-match]").forEach(button => button.onclick = () => rematch(confirmed.filter((_,i) => i !== Number(button.dataset.undoMatch))));
    if (boot.live && options.length) {
      const sources = options.flatMap(group => group.removed.map(item => ({...item,side:group.side,targets:group.added.filter(target => target.type === item.type)}))).filter(item => item.targets.length);
      if (sources.length) {
        const form = document.createElement("div");
        form.className = "manual-match";
        form.innerHTML = `<p class="small muted">Know which nodes correspond? You can match them explicitly, even when there is too little evidence for a suggestion.</p><div class="manual-match-controls"><label>Original node<select id="match-source">${sources.map((source,i) => `<option value="${i}">${escape(sourceLabel + ": " + source.path + " → " + (source.side === "left" ? "A" : "B"))}</option>`).join("")}</select></label><label>New node<select id="match-target"></select></label><button id="manual-match-confirm">Match selected nodes</button></div>`;
        container.querySelector("details").appendChild(form);
        const populateTargets = () => {
          const source = sources[Number($("match-source").value)];
          $("match-target").innerHTML = source.targets.map((target,i) => `<option value="${i}">${escape(target.path)}</option>`).join("");
        };
        $("match-source").onchange = populateTargets;
        populateTargets();
        $("manual-match-confirm").onclick = () => {
          const source = sources[Number($("match-source").value)], target = source.targets[Number($("match-target").value)];
          rematch([...confirmed,{side:source.side,base_path:source.path,target_path:target.path}]);
        };
      }
    }
  }
  // What the server needs to re-compare one file (after an apply or a match change).
  const refreshRequest = f => ({name:f.path, sources:f.sources, three_way:data.three_way, node_matches:f.node_matches || [],
    warnings:f.warnings, extra_warnings:f.extra_warnings});
  function replaceFile(index, updated, extra = {}) {
    const files = data.files.map((item,i) => i === index ? updated : item);
    data = {...data, ...extra, files, summary:{files:files.length, changed_files:files.filter(f => f.status !== "unchanged").length,
      conflicts:files.reduce((n,f) => n + (f.entities.length ? f.entities.filter(e => e.status === "conflict").length : Number(f.status === "conflict")),0),
      warnings:files.reduce((n,f) => n+f.warnings.length,0)}};
  }
  async function rematch(matches) {
    const index = fileIndex, original = file(), path = original.path;
    $("node-matches").querySelectorAll("button").forEach(button => button.disabled = true);
    try {
      const response = await fetch("/api/match",{method:"POST",headers:{"Content-Type":"application/json","X-SceneDiff-Token":boot.token},body:JSON.stringify({...refreshRequest(original),node_matches:matches})});
      const result = await response.json();
      if (!response.ok) throw Error(result.error || "Could not match nodes");
      if (data.files[index] !== original) { toast("The comparison changed while matching. Try again."); return; }
      replaceFile(index, {...result.file, merge_imports:original.merge_imports, merged_sides:original.merged_sides, merge_warnings:original.merge_warnings});
      decisions = Object.fromEntries(Object.entries(decisions).filter(([,choice]) => choice.file !== path));
      selectFile(index);
      toast("Node matching updated. Review decisions for this file were cleared.");
    } catch (error) { toast(error.message); renderNodeMatches(); }
  }
  function renderEntityList(kinds) {
    const items = file()?.entities.filter(e => kinds.includes(e.kind) && visibleEntity(e)) || [];
    return items.map(e => `<button class="entity-card" data-select="${escape(e.key)}"><span class="tree-icon">◇</span><span class="label">${escape(e.label)}<small>${e.rows.filter(r => r.status !== "unchanged").length} changed properties · ${escape(kindLabels[e.kind] || e.kind)}</small></span>${badge(changedLabel(e))}</button>`).join("") || empty("Nothing here", "No matching items in this file.");
  }
  function renderRaw() {
    const f = file();
    if (!f) return "";
    const options = [["diff","A ↔ B diff"],...(data.three_way ? [["0","Base source"]] : []),["1","A source"],["2","B source"]];
    const controls = `<div class="raw-controls">${options.map(([id,label]) => `<button data-raw="${id}" class="${rawMode === id ? "active" : ""}">${label}</button>`).join("")}</div>`;
    if (f.kind === "binary") return `<div class="file-status-card">Binary asset fingerprints${f.sources.map((s,i) => `<p><b>${["Base","A","B"][i]}</b><br><code>${escape(s || "Absent")}</code></p>`).join("")}</div>`;
    const content = rawMode === "diff" ? f.raw_diff : f.sources[Number(rawMode)];
    if (!content) return controls + empty(content === null ? "File absent" : "No text differences", "Select another version to inspect its source.", "▤");
    return controls + `<pre class="code-diff"><code>${content.split("\n").map(line => `<span class="code-line ${rawMode === "diff" ? line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : line.startsWith("@@") ? "hunk" : "" : ""}">${escape(line)}</span>`).join("")}</code></pre>`;
  }
  function reviewTarget() {
    const f = file(), e = entity();
    if (!f || !data.three_way) return null;
    if (!f.entities.length) return f.status !== "unchanged" ? {f,e:null,prop:"$file",values:f.sources} : null;
    if (selectedProperty === "$structure" && e?.structural_conflict) return {f,e,prop:"$structure",values:e.exists.map((exists,i) => exists ? `Keep ${["base","A","B"][i]} structure` : "Remove entity")};
    const row = e?.rows.find(r => r.property === selectedProperty);
    return row && row.status !== "unchanged" ? {f,e,prop:row.property,values:row.values} : null;
  }
  function renderResolution() {
    const target = reviewTarget();
    if (!target) { $("resolution").innerHTML = ""; return; }
    const key = decisionKey(target.f,target.e,target.prop);
    const decision = decisions[key];
    const title = target.prop === "$file" ? "Review file" : target.prop === "$structure" ? "Review structural conflict" : `Review: ${target.prop.replace(/^@/, "")}`;
    $("resolution").innerHTML = `<div class="resolution-card"><h3>${escape(title)} ${decision ? badge("chosen","Decision recorded") : ""}</h3><p>Choose a version for the review plan. To change the scene file, use the Apply buttons above and download the merged result.</p><div class="choice-list">${[1,2].map(i => `<button class="choice ${decision?.choice === ["base","left","right"][i] ? "selected" : ""}" data-choice="${["base","left","right"][i]}"><span class="radio"></span><span>Use ${i === 1 ? "A" : "B"}<small>${escape(target.prop === "$file" ? target.values[i] === null ? "Delete file" : "Whole file version" : concise(target.values[i]))}</small></span></button>`).join("")}</div>${target.prop.startsWith("$") ? "" : `<div class="custom-choice"><input id="custom-value" aria-label="Custom Godot property value" placeholder='Custom value, e.g. Vector2(100, 50)' value="${escape(decision?.choice === "custom" ? decision.value : "")}"><button id="choose-custom">Use custom</button></div>`}<button class="clear-choice" id="clear-choice">${decision ? "Clear decision" : "Leave unresolved"}</button>${target.e?.structural_conflict && target.prop !== "$structure" ? '<button id="review-structure" class="quiet">Review structure →</button>' : ""}</div>`;
    $("resolution").querySelectorAll("[data-choice]").forEach(button => button.onclick = () => choose(button.dataset.choice));
    if ($("choose-custom")) $("choose-custom").onclick = () => {
      const value = $("custom-value").value;
      if (!value.trim()) { toast("Enter a Godot value, or choose A/B to remove an override."); return; }
      choose("custom",value);
    };
    $("clear-choice").onclick = () => { delete decisions[key]; render(); };
    if ($("review-structure")) $("review-structure").onclick = () => { selectedProperty = "$structure"; render(); };
  }
  function choose(choice,value) {
    const target = reviewTarget();
    if (!target) return;
    decisions[decisionKey(target.f,target.e,target.prop)] = {file:target.f.path, entity:target.e?.key || null, property:target.prop, choice,
      value:choice === "custom" ? value : target.prop === "$file" ? undefined : target.values[choice === "left" ? 1 : 2]};
    render();
  }
  function renderInspector() {
    const all = conflicts();
    const resolved = all.filter(c => decisions[c.key]).length;
    $("progress-title").innerHTML = `${resolved}<small> / ${all.length} decisions</small>`;
    $("progress-fill").style.width = `${all.length ? resolved/all.length*100 : 100}%`;
    $("progress-description").textContent = all.length ? `${all.length-resolved} conflict decisions remain. Choices are recorded for review.` : data.three_way ? "No serialized property conflicts found. Review notes and behavior before merging." : "Two-way comparison. Add a common base to detect conflicts.";
    $("next-conflict").disabled = !all.some(c => !decisions[c.key]);
    const f = file(), e = entity();
    if (!f) { $("impact").innerHTML = '<p class="small muted">No changed files.</p>'; $("warnings").innerHTML = ""; return; }
    const existence = e?.exists || f.exists;
    let impact = `<div class="impact-versions">${(data.three_way ? [0,1,2] : [1,2]).map(i => `<span class="${existence[i] ? "" : "missing"}">${["Base","A","B"][i]} ${existence[i] ? "✓" : "—"}</span>`).join("")}</div>`;
    if (e?.lines.some(Boolean)) impact += `<div class="impact-item"><b>SOURCE LOCATION</b>${e.lines.map((line,i) => line ? `${["Base","A","B"][i]}: line ${line}` : "").filter(Boolean).join(" · ")}</div>`;
    if (e?.kind === "node" && e.paths && new Set(e.paths.filter(p => p !== null)).size > 1) impact += `<div class="impact-item"><b>NODE PATHS</b>${e.paths.map((path,i) => path !== null ? `<div>${["Base","A","B"][i]}: <code>${escape(path)}</code></div>` : "").join("")}</div>`;
    if (e?.used_by.some(users => users.length)) impact += `<div class="impact-item"><b>RESOURCE REFERENCES</b>${e.used_by.map((users,i) => users.length ? `<p><b>${["Base","A","B"][i]}</b>${users.map(u => `<code>${escape(u)}</code>`).join("<br>")}</p>` : "").join("")}</div>`;
    if (f.referenced_by?.length) impact += `<div class="impact-item"><b>REFERENCED BY · BRANCH B</b>${f.referenced_by.map(p => `<code>${escape(p)}</code>`).join("<br>")}</div>`;
    if (!e?.used_by.some(users => users.length) && !f.referenced_by?.length) impact += '<p class="small muted">Inspect the serialized properties and raw source to understand the scope of this change.</p>';
    const homes = resourceHomes(f.entities);
    if (e?.kind === "node") {
      const own = f.entities.filter(r => homes.get(r.key) === e.key);
      if (own.length) impact += `<div class="impact-item"><b>COMES WITH THIS NODE</b>${own.map(r => `<div>${escape(r.label)} ${badge(changedLabel(r))}</div>`).join("")}<span class="muted">Applying the whole node copies these too.</span></div>`;
    } else if (e && homes.has(e.key)) {
      const home = f.entities.find(n => n.key === homes.get(e.key));
      if (home) impact += `<div class="impact-item"><b>BELONGS TO NODE</b><code>${escape(home.label)}</code><br><span class="muted">It is copied when that node is applied.</span></div>`;
    }
    $("impact").innerHTML = impact;
    const warnings = [...f.warnings,...(f.merge_warnings || []),...(e?.notes || [])];
    if (data.context.merge_preview && data.context.source === "git") warnings.push("Git commit IDs identify the original snapshots. Validate merged files and their dependencies in your target project.");
    if (e?.kind === "order") warnings.push("Child order can affect UI layout and processing. Review the complete order when both branches add children.");
    if (e?.rows.some(row => row.property === "@instance")) warnings.push("Inherited values and runtime behavior are not expanded here.");
    $("warnings").innerHTML = warnings.map(w => `<div class="warning">${escape(w)}</div>`).join("") || '<p class="small muted">No parser or reference warnings for this file. Runtime behavior has not been validated in Godot.</p>';
    $("footer-status").textContent = `${data.summary.changed_files} changed files · ${all.length-resolved} pending conflict decisions`;
  }
  function render() {
    if (!data) return;
    $("project-name").textContent = data.context.repository || "Local comparison";
    $("base-label").textContent = data.three_way ? data.labels[0] : "Not provided";
    $("a-label").textContent = data.labels[1]; $("b-label").textContent = data.labels[2];
    $("source-badge").textContent = data.context.merge_preview ? "MERGE PREVIEW" : data.context.source === "demo" ? "EXAMPLE PROJECT" : data.context.source === "git" ? "COMMITTED SNAPSHOTS" : "LOCAL FILES";
    $("new-comparison").textContent = boot.live ? "＋ New comparison" : "Open local app";
    renderSidebar();
    const f = file(), e = entity();
    const additions = data.files.flatMap(f => f.entities).filter(e => changedLabel(e) === "added" && e.kind === "node").length;
    $("summary").innerHTML = `<span><strong>${data.summary.changed_files}</strong> changed files</span><span class="red"><strong>${data.summary.conflicts}</strong> conflict items</span><span class="green"><strong>${additions}</strong> added nodes</span>`;
    $("breadcrumb").textContent = f?.path || "Project comparison";
    $("entity-title").textContent = e?.label || f?.path.split("/").pop() || "Everything is in sync";
    $("entity-subtitle").innerHTML = f ? `${escape(e ? kindLabels[e.kind] || e.kind : f.kind === "text" ? "Text file" : f.kind)} ${badge(changedLabel(e || f))}${data.three_way ? "" : '<span class="muted">Two-way comparison</span>'}` : "No committed differences between these snapshots.";
    document.querySelectorAll("[data-tab]").forEach(button => button.setAttribute("aria-selected",button.dataset.tab === tab));
    $("unchanged-toggle").hidden = tab !== "properties";
    if (!f) $("comparison").innerHTML = empty("No changes to review", "These snapshots contain the same committed files.", "✓");
    else if (tab === "properties") $("comparison").innerHTML = renderProperties();
    else if (tab === "resources") $("comparison").innerHTML = renderEntityList(["ext_resource","sub_resource","resource"]);
    else if (tab === "signals") $("comparison").innerHTML = renderEntityList(["connection"]);
    else $("comparison").innerHTML = renderRaw();
    $("comparison").querySelectorAll("[data-property]").forEach(row => {
      const activate = () => { selectedProperty = row.dataset.property; render(); };
      row.onclick = activate; row.onkeydown = event => { if (["Enter"," "].includes(event.key)) { event.preventDefault(); activate(); } };
    });
    $("comparison").querySelectorAll("[data-select]").forEach(button => button.onclick = () => selectEntity(button.dataset.select));
    $("comparison").querySelectorAll("[data-raw]").forEach(button => button.onclick = () => { rawMode = button.dataset.raw; render(); });
    renderMerge(); renderNodeMatches(); renderResolution(); renderInspector();
  }
  function download(name,contents,type) {
    const url = URL.createObjectURL(new Blob([contents],{type}));
    const anchor = document.createElement("a"); anchor.href=url; anchor.download=name; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url),1000);
  }
  function renderMerge() {
    const f = file(), e = entity();
    const supported = f && ["scene", "resource"].includes(f.kind);
    const row = e?.rows.find(r => r.property === selectedProperty);
    const protectedProps = {node:["@name","@parent","@unique_id","@type","@instance"], gd_scene:["@format","@uid"], gd_resource:["@format","@uid","@type"]};
    const canProperty = row && e.exists[1] && e.exists[2] && !(protectedProps[e.kind] || []).includes(row.property);
    const isRoot = e?.kind === "node" && e.key === "node:.";
    const canEntity = e && !["gd_scene","gd_resource"].includes(e.kind) && !(isRoot && e.exists[1] && e.exists[2]);
    const canAll = e?.kind === "node" && e.exists[1] && e.exists[2];
    const buttons = scope => [1,2].map(source => {
      const target = 3-source;
      const disabled = merging || f.sources[target] === null || (scope === "property" && !canProperty) || (scope === "all" && !canAll) || (scope === "entity" && (!canEntity || (e.kind === "node" && e.path === "." && !e.exists[source])));
      return `<button data-apply="${source}" data-scope="${scope}" ${disabled ? "disabled" : ""}>Apply ${source === 1 ? "A → B" : "B → A"}</button>`;
    }).join("");
    let html = "";
    if (supported && boot.live && e) {
      html = `<div class="resolution-card"><h3>Apply changes to a scene</h3><p>Start with the target version and copy only what you select. Download the result when you are ready.</p>`;
      if (row) html += `<div class="merge-scope">Selected property: <strong>${escape(row.property.replace(/^@/,""))}</strong></div><div class="merge-actions">${buttons("property")}</div>`;
      if (canAll) html += `<div class="merge-scope">All properties of this node</div><div class="merge-actions">${buttons("all")}</div><p>Copies every property of this node and keeps its children, name and place in the scene.${isRoot ? " To bring over child nodes, select them in the outline." : ""}</p>`;
      if (canEntity) html += `<div class="merge-scope">${e.kind === "node" ? "Whole node and children" : "Whole " + escape(kindLabels[e.kind] || e.kind)}</div><div class="merge-actions">${buttons("entity")}</div><p>${e.kind === "node" ? "Replaces this target subtree, including its signal connections, with the source subtree. Its textures, shaders, materials and meshes come with it. An absent source removes the target subtree. You are asked to confirm before anything only the target has is removed." : "Copies the selected item. An absent source removes it when no references remain."}</p>`;
      if (["sub_resource","ext_resource"].includes(e.kind)) html += '<p>Editing this resource affects every node that references it in the target.</p>';
      html += '</div>';
    } else if (supported && !boot.live) {
      html = '<div class="inline-note">This saved report can download existing merged results. Run “python -m scenediff” to apply more changes.</div>';
    }
    $("merge-controls").innerHTML = html;
    $("merge-controls").querySelectorAll("[data-apply]").forEach(button => button.onclick = () => applyMerge(Number(button.dataset.apply), button.dataset.scope));
    $("merge-error").textContent = mergeError;
    $("merge-status").textContent = merging ? "Applying change…" : f?.merged_sides?.length ? "The selected file has a merged preview. Download it and validate it in Godot. Referenced asset files must be present in your project." : "Apply a change, then download the updated scene.";
    $("download-a").disabled = merging || !f?.merged_sides?.includes(1);
    $("download-b").disabled = merging || !f?.merged_sides?.includes(2);
    $("undo-merge").disabled = merging || !mergeHistory.length;
    $("new-comparison").disabled = merging;
    $("save-report").disabled = merging;
  }
  async function applyMerge(source, scope, confirmed = false) {
    if (merging) return;
    const index = fileIndex, f = file(), e = entity();
    if (!f || !e || !boot.live) return;
    const property = scope === "property" ? selectedProperty : scope === "all" ? "*" : null;
    const previous = {data, decisions:{...decisions}, index, entityKey, selectedProperty};
    merging = true; mergeError = ""; renderMerge();
    try {
      const response = await fetch('/api/merge', {method:'POST', headers:{'Content-Type':'application/json','X-SceneDiff-Token':boot.token}, body:JSON.stringify({...refreshRequest(f), source, entity:e.key, property, confirm:confirmed, imports:f.merge_imports?.[source] || []})});
      const result = await response.json();
      if (response.status === 409 && result.confirm) {
        const list = result.confirm.slice(0, 12).map(item => "• " + item).join("\n") + (result.confirm.length > 12 ? `\n…and ${result.confirm.length - 12} more` : "");
        merging = false;
        if (window.confirm(`Replacing ${e.label} with the ${source === 1 ? "A" : "B"} version removes what only ${source === 1 ? "B" : "A"} has:\n\n${list}\n\nReplace it anyway? (Undo last apply can restore it.)`)) return await applyMerge(source, scope, true);
        return;
      }
      if (!response.ok) throw Error(result.error || 'Merge failed');
      const updated = result.file;
      updated.merge_imports = {...f.merge_imports, [source]:result.merge.imports};
      updated.merged_sides = [...new Set([...(f.merged_sides || []),result.merge.target])];
      updated.merge_warnings = [...new Set([...(f.merge_warnings || []),...result.merge.warnings])];
      mergeHistory.push(previous);
      if (mergeHistory.length > 20) mergeHistory.shift();
      replaceFile(index, updated, {id:'merge-' + data.id.replace(/^merge-/, '') + '-' + mergeHistory.length, context:{...data.context, merge_preview:true},
        applied_changes:[...(data.applied_changes || []), {file:f.path, entity:e.key, property, source, target:result.merge.target}]});
      decisions = Object.fromEntries(Object.entries(decisions).filter(([,d]) => d.file !== f.path));
      fileIndex=index; entityKey=updated.entities.some(item => item.key === e.key) ? e.key : updated.entities[0]?.key;
      selectedProperty=null; tab='properties'; statusFilter="all";
      toast(`Applied ${source === 1 ? 'A → B' : 'B → A'}. Download merged ${source === 1 ? 'B' : 'A'} to save it.`);
    } catch (error) { mergeError=error.message; }
    finally { merging=false; render(); }
  }
  function downloadMerged(side) {
    const f=file();
    if (!f?.merged_sides?.includes(side)) return;
    const name=f.path.split(/[\\/]/).pop().replace(/(\.(?:tscn|tres))$/i, `.merged-${side === 1 ? 'A' : 'B'}$1`);
    download(name,f.sources[side],'text/plain;charset=utf-8');
  }
  $("download-a").onclick=() => downloadMerged(1);
  $("download-b").onclick=() => downloadMerged(2);
  $("undo-merge").onclick=() => {
    if (merging || !mergeHistory.length) return;
    const previous=mergeHistory.pop();
    data=previous.data; decisions=previous.decisions; fileIndex=previous.index; entityKey=previous.entityKey; selectedProperty=previous.selectedProperty;
    mergeError=""; render(); toast('Last apply undone.');
  };
  function reviewPlan() {
    return {format:"scenediff-review-plan",version:1,report_id:data.id,created_at:new Date().toISOString(),labels:data.labels,context:data.context,
      note:"Review-plan decisions do not apply edits. Explicit Apply operations update the downloadable preview separately. Godot runtime validation and custom-value validation are not performed.",
      applied_changes:data.applied_changes || [],
      node_matches:data.files.filter(f => f.node_matches?.length).map(f => ({file:f.path,matches:f.node_matches})),
      decisions:Object.values(decisions),unresolved:conflicts().filter(c => !decisions[c.key]).map(c => ({file:c.f.path,entity:c.e?.key || null,property:c.prop}))};
  }
  $("export-plan").onclick = () => download("scenediff-review.json",JSON.stringify(reviewPlan(),null,2),"application/json");
  $("save-report").onclick = () => {
    const clone = document.documentElement.cloneNode(true);
    clone.querySelector("#boot").textContent = JSON.stringify({live:false,token:"",report:{...data,review:{decisions}}}).replace(/</g,"\\u003c").replace(/>/g,"\\u003e").replace(/&/g,"\\u0026");
    clone.querySelector("#compare-dialog").removeAttribute("open");
    clone.querySelector("#toast").hidden = true;
    // Do not retain local paths or branch inputs from the comparison dialog.
    clone.querySelectorAll("#compare-form input").forEach(input => input.removeAttribute("value"));
    download("scenediff-report.html","<!doctype html>\n"+clone.outerHTML,"text/html");
    toast("Saved a self-contained report with your review decisions.");
  };
  $("next-conflict").onclick = () => {
    const next = conflicts().find(c => !decisions[c.key]);
    if (!next) return;
    fileIndex=data.files.indexOf(next.f); entityKey=next.e?.key || null; selectedProperty=next.prop;
    tab=next.e ? "properties" : "raw"; query=""; $("search").value=""; render();
    $("resolution").scrollIntoView({behavior:"smooth",block:"nearest"});
  };
  $("search").oninput = event => { query=event.target.value.toLowerCase(); renderSidebar(); };
  $("show-unchanged").onchange = event => { showUnchanged=event.target.checked; render(); };
  document.querySelectorAll("[data-tab]").forEach(button => button.onclick = () => { tab=button.dataset.tab; render(); });
  document.querySelectorAll("[data-mode]").forEach(button => button.onclick = () => {
    mode=button.dataset.mode;
    $("git-fields").hidden=mode !== "git"; $("file-fields").hidden=mode !== "files";
    document.querySelectorAll("[data-mode]").forEach(b => b.classList.toggle("active",b.dataset.mode === mode));
    $("form-error").textContent="";
  });
  $("new-comparison").onclick = () => {
    if (merging) return;
    if (!boot.live) { toast("Run “scenediff serve” on your computer to start a new comparison."); return; }
    $("compare-dialog").showModal();
  };
  $("close-dialog").onclick = () => $("compare-dialog").close();
  $("compare-form").onsubmit = async event => {
    event.preventDefault(); $("form-error").textContent=""; $("submit-compare").disabled=true; $("submit-compare").textContent="Comparing…";
    try {
      let payload;
      if (mode === "git") {
        payload=Object.fromEntries(new FormData(event.target));
        if (!payload.repo || !payload.left || !payload.right) throw Error("Enter the repository path and both branches.");
      } else {
        const a=$("file-a").files[0], b=$("file-b").files[0], base=$("file-base").files[0];
        if (!a) throw Error("Choose version A, or a single file containing Git conflict markers.");
        if ([a,b,base].some(f => f && f.size > 8*1024*1024)) throw Error("Each file must be 8 MiB or smaller.");
        payload={left:await a.text(),right:b ? await b.text() : null,base:base ? await base.text() : null,name:(b || a).name};
      }
      const response=await fetch(`/api/${mode}`,{method:"POST",headers:{"Content-Type":"application/json","X-SceneDiff-Token":boot.token},body:JSON.stringify(payload)});
      const result=await response.json(); if (!response.ok) throw Error(result.error || "Comparison failed");
      data=result; decisions={}; mergeHistory=[]; mergeError=""; query=""; $("search").value=""; statusFilter="all";
      selectFile(0); $("compare-dialog").close();
    } catch (error) { $("form-error").textContent=error.message; }
    finally { $("submit-compare").disabled=false; $("submit-compare").textContent="Compare changes →"; }
  };
  document.addEventListener("keydown",event => {
    if (event.ctrlKey || event.metaKey || event.altKey || $("compare-dialog").open || ["INPUT","TEXTAREA","SELECT"].includes(document.activeElement.tagName)) return;
    if (event.key.toLowerCase() === "a") choose("left");
    if (event.key.toLowerCase() === "b") choose("right");
  });
  selectFile(data?.files.findIndex(f => f.status === "conflict") >= 0 ? data.files.findIndex(f => f.status === "conflict") : 0);
})();

