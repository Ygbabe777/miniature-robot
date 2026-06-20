// ═══════════════════════════════════════════════════
//  AGX DEALER MECHANICS — app.js
// ═══════════════════════════════════════════════════

const PINE_CODE = `// This source code is subject to the terms of the Mozilla Public License 2.0
// © AGX Dealer Mechanics — Pine Script v6
//@version=6
indicator("AGX Dealer Mechanics", overlay=true, max_lines_count=500, max_boxes_count=200, max_labels_count=200)

// ═══════════════════════════════════════════════════════════════════
// INPUT — Gruppo: Regime & Zero Gamma
// ═══════════════════════════════════════════════════════════════════
i_net_gex_pos   = input.bool(true,  "Net GEX è positivo?",      group="Regime & Zero Gamma",
                              tooltip="TRUE = dealer in gamma positiva (fade edges); FALSE = gamma negativa (follow momentum)")
i_zero_gamma    = input.float(0.0,  "Zero Gamma / HVL (prezzo)", group="Regime & Zero Gamma", step=0.25)
i_max_change    = input.string("Neutro", "Max Change GEX",       group="Regime & Zero Gamma",
                              options=["Neutro","Tutto rosso (bearish)","Tutto verde (bullish)"])

// ═══════════════════════════════════════════════════════════════════
// INPUT — Gruppo: Nodi Maggiori GEXBot (metodo Freddy)
// ═══════════════════════════════════════════════════════════════════
i_g1p = input.float(0.0, "Green 1 — Prezzo", group="Nodi GEXBot (metodo Freddy)", step=0.25)
i_g1s = input.float(0.0, "Green 1 — Size ($M)", group="Nodi GEXBot (metodo Freddy)")
i_g2p = input.float(0.0, "Green 2 — Prezzo", group="Nodi GEXBot (metodo Freddy)", step=0.25)
i_g2s = input.float(0.0, "Green 2 — Size ($M)", group="Nodi GEXBot (metodo Freddy)")
i_g3p = input.float(0.0, "Green 3 — Prezzo", group="Nodi GEXBot (metodo Freddy)", step=0.25)
i_g3s = input.float(0.0, "Green 3 — Size ($M)", group="Nodi GEXBot (metodo Freddy)")
i_g4p = input.float(0.0, "Green 4 — Prezzo", group="Nodi GEXBot (metodo Freddy)", step=0.25)
i_g4s = input.float(0.0, "Green 4 — Size ($M)", group="Nodi GEXBot (metodo Freddy)")

i_r1p = input.float(0.0, "Red 1 — Prezzo", group="Nodi GEXBot (metodo Freddy)", step=0.25)
i_r1s = input.float(0.0, "Red 1 — Size ($M)", group="Nodi GEXBot (metodo Freddy)")
i_r2p = input.float(0.0, "Red 2 — Prezzo", group="Nodi GEXBot (metodo Freddy)", step=0.25)
i_r2s = input.float(0.0, "Red 2 — Size ($M)", group="Nodi GEXBot (metodo Freddy)")
i_r3p = input.float(0.0, "Red 3 — Prezzo", group="Nodi GEXBot (metodo Freddy)", step=0.25)
i_r3s = input.float(0.0, "Red 3 — Size ($M)", group="Nodi GEXBot (metodo Freddy)")
i_r4p = input.float(0.0, "Red 4 — Prezzo", group="Nodi GEXBot (metodo Freddy)", step=0.25)
i_r4s = input.float(0.0, "Red 4 — Size ($M)", group="Nodi GEXBot (metodo Freddy)")

// ═══════════════════════════════════════════════════════════════════
// INPUT — Gruppo: Livelli MenthorQ
// ═══════════════════════════════════════════════════════════════════
i_mq_cr     = input.float(0.0, "Call Resistance",      group="Livelli MenthorQ", step=0.25)
i_mq_ps     = input.float(0.0, "Put Support",          group="Livelli MenthorQ", step=0.25)
i_mq_hvl    = input.float(0.0, "HVL",                  group="Livelli MenthorQ", step=0.25)
i_mq_cr0    = input.float(0.0, "Call Resistance 0DTE", group="Livelli MenthorQ", step=0.25)
i_mq_ps0    = input.float(0.0, "Put Support 0DTE",     group="Livelli MenthorQ", step=0.25)
i_mq_gw0    = input.float(0.0, "Gamma Wall 0DTE",      group="Livelli MenthorQ", step=0.25)
i_mq_1dmin  = input.float(0.0, "1D Min",               group="Livelli MenthorQ", step=0.25)
i_mq_1dmax  = input.float(0.0, "1D Max",               group="Livelli MenthorQ", step=0.25)

i_gx1p = input.float(0.0, "GEX Level 1 — Prezzo", group="Livelli MenthorQ", step=0.25)
i_gx1l = input.string("GEX1", "GEX Level 1 — Label", group="Livelli MenthorQ")
i_gx2p = input.float(0.0, "GEX Level 2 — Prezzo", group="Livelli MenthorQ", step=0.25)
i_gx2l = input.string("GEX2", "GEX Level 2 — Label", group="Livelli MenthorQ")
i_gx3p = input.float(0.0, "GEX Level 3 — Prezzo", group="Livelli MenthorQ", step=0.25)
i_gx3l = input.string("GEX3", "GEX Level 3 — Label", group="Livelli MenthorQ")
i_gx4p = input.float(0.0, "GEX Level 4 — Prezzo", group="Livelli MenthorQ", step=0.25)
i_gx4l = input.string("GEX4", "GEX Level 4 — Label", group="Livelli MenthorQ")
i_gx5p = input.float(0.0, "GEX Level 5 — Prezzo", group="Livelli MenthorQ", step=0.25)
i_gx5l = input.string("GEX5", "GEX Level 5 — Label", group="Livelli MenthorQ")

// ═══════════════════════════════════════════════════════════════════
// INPUT — Gruppo: Livelli QQQ Correlati
// ═══════════════════════════════════════════════════════════════════
i_qq1p = input.float(0.0, "QQQ Level 1 — Prezzo", group="Livelli QQQ Correlati", step=0.25)
i_qq1l = input.string("QQQ1", "QQQ Level 1 — Label", group="Livelli QQQ Correlati")
i_qq2p = input.float(0.0, "QQQ Level 2 — Prezzo", group="Livelli QQQ Correlati", step=0.25)
i_qq2l = input.string("QQQ2", "QQQ Level 2 — Label", group="Livelli QQQ Correlati")
i_qq3p = input.float(0.0, "QQQ Level 3 — Prezzo", group="Livelli QQQ Correlati", step=0.25)
i_qq3l = input.string("QQQ3", "QQQ Level 3 — Label", group="Livelli QQQ Correlati")

// ═══════════════════════════════════════════════════════════════════
// INPUT — Gruppo: Parametri di Calcolo
// ═══════════════════════════════════════════════════════════════════
i_tol_agx    = input.float(15.0,  "Tolleranza confluenza AGX Zone (punti)", group="Parametri di Calcolo",
                            tooltip="Distanza massima in punti tra livelli per essere raggruppati in una AGX Zone")
i_buf_flip   = input.float(10.0,  "Buffer rottura con momentum (punti)",    group="Parametri di Calcolo",
                            tooltip="Punti oltre il livello necessari per confermare il flip di un nodo")
i_pin_narrow = input.float(300.0, "Soglia pin NARROW (punti)",              group="Parametri di Calcolo")
i_pin_wide   = input.float(600.0, "Soglia pin WIDE (punti)",                group="Parametri di Calcolo")

// ═══════════════════════════════════════════════════════════════════
// INPUT — Gruppo: Visualizzazione
// ═══════════════════════════════════════════════════════════════════
i_show_table = input.bool(true, "Mostra tabella riepilogativa", group="Visualizzazione")
i_table_pos  = input.string("Top Right", "Posizione tabella", group="Visualizzazione",
                             options=["Top Right","Top Left","Bottom Right","Bottom Left","Top Center","Bottom Center"])

// ═══════════════════════════════════════════════════════════════════
// COSTANTI DI COLORE
// ═══════════════════════════════════════════════════════════════════
COLOR_GREEN       = color.new(#00C853, 0)
COLOR_GREEN_T     = color.new(#00C853, 80)
COLOR_RED         = color.new(#D32F2F, 0)
COLOR_RED_T       = color.new(#D32F2F, 80)
COLOR_YELLOW      = color.new(#FFD600, 0)
COLOR_YELLOW_T    = color.new(#FFD600, 85)
COLOR_BLUE_L      = color.new(#29B6F6, 0)
COLOR_BLUE_LT     = color.new(#29B6F6, 80)
COLOR_ORANGE      = color.new(#FF6D00, 0)
COLOR_ORANGE_T    = color.new(#FF6D00, 75)
COLOR_WHITE       = color.white
COLOR_GRAY        = color.new(color.gray, 30)
COLOR_BG_FADE     = color.new(#00C853, 85)
COLOR_BG_FOLLOW   = color.new(#D32F2F, 85)
COLOR_BG_TRANS    = color.new(#FFD600, 85)

// ═══════════════════════════════════════════════════════════════════
// STATO PERSISTENTE — reset ad ogni nuova sessione/giorno
// ═══════════════════════════════════════════════════════════════════
// flip: indici 0-3 = green nodes 1-4, indici 4-7 = red nodes 1-4
var array<bool>  gex_flipped      = array.new<bool>(8, false)
var array<int>   zone_touch_count = array.new<int>(0)
var array<float> zone_prices_prev = array.new<float>(0)
var int          last_reset_day   = -1

is_new_day = dayofmonth != last_reset_day

if is_new_day and barstate.isconfirmed
    last_reset_day := dayofmonth
    for i = 0 to 7
        array.set(gex_flipped, i, false)
    array.clear(zone_touch_count)
    array.clear(zone_prices_prev)

// ═══════════════════════════════════════════════════════════════════
// FUNZIONI
// ═══════════════════════════════════════════════════════════════════
f_is_active(p) =>
    p != 0.0

f_check_flip(price, is_green, idx) =>
    current = array.get(gex_flipped, idx)
    new_flip = current
    if not current and f_is_active(price)
        if is_green and close < (price - i_buf_flip)
            new_flip := true
        else if not is_green and close > (price + i_buf_flip)
            new_flip := true
    array.set(gex_flipped, idx, new_flip)
    not current and new_flip

f_star_rating(n) =>
    n >= 5 ? "★★★★★" :
     n == 4 ? "★★★★" :
     n == 3 ? "★★★" :
     n == 2 ? "★★" : "★"

// ═══════════════════════════════════════════════════════════════════
// CALCOLO REGIME
// HARD RULE: "FOLLOW MOMENTUM" non può MAI essere assegnato se
// i_net_gex_pos == true. In gamma positiva i dealer sono sempre
// mean-reverting, mai trend-following — questo vincolo è strutturale.
// ═══════════════════════════════════════════════════════════════════
calc_mode() =>
    if i_net_gex_pos and close > i_zero_gamma
        "FADE EDGES"
    else if not i_net_gex_pos and close < i_zero_gamma
        "FOLLOW MOMENTUM"
    else
        "TRANSIZIONE"

current_mode = calc_mode()

// ═══════════════════════════════════════════════════════════════════
// STATI FLIP (aggiornati su ogni barra confermata)
// ═══════════════════════════════════════════════════════════════════
flip_g1 = barstate.isconfirmed ? f_check_flip(i_g1p, true,  0) : false
flip_g2 = barstate.isconfirmed ? f_check_flip(i_g2p, true,  1) : false
flip_g3 = barstate.isconfirmed ? f_check_flip(i_g3p, true,  2) : false
flip_g4 = barstate.isconfirmed ? f_check_flip(i_g4p, true,  3) : false
flip_r1 = barstate.isconfirmed ? f_check_flip(i_r1p, false, 4) : false
flip_r2 = barstate.isconfirmed ? f_check_flip(i_r2p, false, 5) : false
flip_r3 = barstate.isconfirmed ? f_check_flip(i_r3p, false, 6) : false
flip_r4 = barstate.isconfirmed ? f_check_flip(i_r4p, false, 7) : false

any_flip = flip_g1 or flip_g2 or flip_g3 or flip_g4 or flip_r1 or flip_r2 or flip_r3 or flip_r4

// ═══════════════════════════════════════════════════════════════════
// COSTRUZIONE LISTA LIVELLI PER CLUSTERING
// ═══════════════════════════════════════════════════════════════════
var array<float>  all_prices  = array.new<float>()
var array<string> all_sources = array.new<string>()

if barstate.islast
    array.clear(all_prices)
    array.clear(all_sources)

    if f_is_active(i_g1p) => array.push(all_prices, i_g1p) => array.push(all_sources, "GEXBot Green")
    if f_is_active(i_g2p) => array.push(all_prices, i_g2p) => array.push(all_sources, "GEXBot Green")
    if f_is_active(i_g3p) => array.push(all_prices, i_g3p) => array.push(all_sources, "GEXBot Green")
    if f_is_active(i_g4p) => array.push(all_prices, i_g4p) => array.push(all_sources, "GEXBot Green")
    if f_is_active(i_r1p) => array.push(all_prices, i_r1p) => array.push(all_sources, "GEXBot Red")
    if f_is_active(i_r2p) => array.push(all_prices, i_r2p) => array.push(all_sources, "GEXBot Red")
    if f_is_active(i_r3p) => array.push(all_prices, i_r3p) => array.push(all_sources, "GEXBot Red")
    if f_is_active(i_r4p) => array.push(all_prices, i_r4p) => array.push(all_sources, "GEXBot Red")
    if f_is_active(i_mq_cr)    => array.push(all_prices, i_mq_cr)    => array.push(all_sources, "MQ Call Resistance")
    if f_is_active(i_mq_ps)    => array.push(all_prices, i_mq_ps)    => array.push(all_sources, "MQ Put Support")
    if f_is_active(i_mq_hvl)   => array.push(all_prices, i_mq_hvl)   => array.push(all_sources, "MQ HVL")
    if f_is_active(i_mq_cr0)   => array.push(all_prices, i_mq_cr0)   => array.push(all_sources, "MQ CR 0DTE")
    if f_is_active(i_mq_ps0)   => array.push(all_prices, i_mq_ps0)   => array.push(all_sources, "MQ PS 0DTE")
    if f_is_active(i_mq_gw0)   => array.push(all_prices, i_mq_gw0)   => array.push(all_sources, "MQ GW 0DTE")
    if f_is_active(i_mq_1dmin) => array.push(all_prices, i_mq_1dmin) => array.push(all_sources, "MQ 1D Min")
    if f_is_active(i_mq_1dmax) => array.push(all_prices, i_mq_1dmax) => array.push(all_sources, "MQ 1D Max")
    if f_is_active(i_gx1p) => array.push(all_prices, i_gx1p) => array.push(all_sources, i_gx1l)
    if f_is_active(i_gx2p) => array.push(all_prices, i_gx2p) => array.push(all_sources, i_gx2l)
    if f_is_active(i_gx3p) => array.push(all_prices, i_gx3p) => array.push(all_sources, i_gx3l)
    if f_is_active(i_gx4p) => array.push(all_prices, i_gx4p) => array.push(all_sources, i_gx4l)
    if f_is_active(i_gx5p) => array.push(all_prices, i_gx5p) => array.push(all_sources, i_gx5l)
    if f_is_active(i_qq1p) => array.push(all_prices, i_qq1p) => array.push(all_sources, i_qq1l)
    if f_is_active(i_qq2p) => array.push(all_prices, i_qq2p) => array.push(all_sources, i_qq2l)
    if f_is_active(i_qq3p) => array.push(all_prices, i_qq3p) => array.push(all_sources, i_qq3l)
    if f_is_active(i_zero_gamma) => array.push(all_prices, i_zero_gamma) => array.push(all_sources, "Zero Gamma")

// ═══════════════════════════════════════════════════════════════════
// CLUSTERING AGX ZONES (greedy su array ordinato)
// ═══════════════════════════════════════════════════════════════════
var array<float>  cluster_prices  = array.new<float>()
var array<int>    cluster_counts  = array.new<int>()
var array<string> cluster_sources = array.new<string>()

if barstate.islast
    array.clear(cluster_prices)
    array.clear(cluster_counts)
    array.clear(cluster_sources)
    n = array.size(all_prices)
    if n > 0
        sorted_p = array.copy(all_prices)
        sorted_s = array.copy(all_sources)
        // Bubble sort (n ≤ ~30, prestazioni accettabili)
        for i = 0 to n - 2
            for j = 0 to n - 2 - i
                if array.get(sorted_p, j) > array.get(sorted_p, j + 1)
                    tmp_p = array.get(sorted_p, j)
                    array.set(sorted_p, j, array.get(sorted_p, j + 1))
                    array.set(sorted_p, j + 1, tmp_p)
                    tmp_s = array.get(sorted_s, j)
                    array.set(sorted_s, j, array.get(sorted_s, j + 1))
                    array.set(sorted_s, j + 1, tmp_s)
        cluster_start_price = array.get(sorted_p, 0)
        cluster_sum         = cluster_start_price
        cluster_cnt         = 1
        cluster_src         = array.get(sorted_s, 0)
        for i = 1 to n - 1
            p_curr = array.get(sorted_p, i)
            s_curr = array.get(sorted_s, i)
            if p_curr - cluster_start_price <= i_tol_agx
                cluster_sum += p_curr
                cluster_cnt += 1
                cluster_src := cluster_src + ", " + s_curr
            else
                if cluster_cnt >= 2
                    array.push(cluster_prices,  cluster_sum / cluster_cnt)
                    array.push(cluster_counts,  cluster_cnt)
                    array.push(cluster_sources, cluster_src)
                cluster_start_price := p_curr
                cluster_sum         := p_curr
                cluster_cnt         := 1
                cluster_src         := s_curr
        if cluster_cnt >= 2
            array.push(cluster_prices,  cluster_sum / cluster_cnt)
            array.push(cluster_counts,  cluster_cnt)
            array.push(cluster_sources, cluster_src)

if barstate.islast
    n_zones = array.size(cluster_prices)
    cur_cnt = array.size(zone_touch_count)
    if n_zones != cur_cnt or array.size(zone_prices_prev) != n_zones
        array.clear(zone_touch_count)
        array.clear(zone_prices_prev)
        for i = 0 to n_zones - 1
            array.push(zone_touch_count, 0)
            array.push(zone_prices_prev, array.get(cluster_prices, i))

// ═══════════════════════════════════════════════════════════════════
// DISEGNO — cancella e ridisegna su barstate.islast
// ═══════════════════════════════════════════════════════════════════
var array<line>  drawn_lines  = array.new<line>()
var array<box>   drawn_boxes  = array.new<box>()
var array<label> drawn_labels = array.new<label>()

f_clear_drawings() =>
    for l in drawn_lines   => line.delete(l)
    for b in drawn_boxes   => box.delete(b)
    for lb in drawn_labels => label.delete(lb)
    array.clear(drawn_lines)
    array.clear(drawn_boxes)
    array.clear(drawn_labels)

f_hline(price, col, lw, lstyle) =>
    l = line.new(bar_index - 1, price, bar_index, price,
                 extend=extend.right, color=col, width=lw, style=lstyle)
    array.push(drawn_lines, l)

f_hlabel(price, txt, col, txt_col) =>
    lb = label.new(bar_index, price, txt,
                   xloc=xloc.bar_index, yloc=yloc.price,
                   color=col, textcolor=txt_col,
                   style=label.style_label_left, size=size.small)
    array.push(drawn_labels, lb)

if barstate.islast
    f_clear_drawings()

    f_gex_width(s) =>
        s <= 0 ? 1 : s < 500 ? 1 : s < 1000 ? 2 : s < 2000 ? 3 : 4

    f_draw_gex_node(price, size_m, idx, is_green) =>
        if f_is_active(price)
            flipped  = array.get(gex_flipped, idx)
            base_col = is_green ? COLOR_GREEN : COLOR_RED
            lstyle   = flipped ? line.style_dashed : line.style_solid
            lw       = f_gex_width(size_m)
            f_hline(price, base_col, lw, lstyle)
            flip_tag  = flipped ? (is_green ? " [⟳ RES]" : " [⟳ SUP]") : ""
            size_tag  = size_m > 0 ? " $" + str.tostring(size_m, "#,###") + "M" : ""
            lbl_col   = flipped ? (is_green ? COLOR_RED_T : COLOR_GREEN_T) : color.new(base_col, 80)
            f_hlabel(price, (is_green ? "G" : "R") + str.tostring(idx mod 4 + 1) + size_tag + flip_tag, lbl_col, base_col)

    f_draw_gex_node(i_g1p, i_g1s, 0, true)
    f_draw_gex_node(i_g2p, i_g2s, 1, true)
    f_draw_gex_node(i_g3p, i_g3s, 2, true)
    f_draw_gex_node(i_g4p, i_g4s, 3, true)
    f_draw_gex_node(i_r1p, i_r1s, 4, false)
    f_draw_gex_node(i_r2p, i_r2s, 5, false)
    f_draw_gex_node(i_r3p, i_r3s, 6, false)
    f_draw_gex_node(i_r4p, i_r4s, 7, false)

    if f_is_active(i_mq_cr)
        f_hline(i_mq_cr, COLOR_RED, 2, line.style_solid)
        f_hlabel(i_mq_cr, "MQ Call Res", color.new(COLOR_RED, 80), COLOR_RED)
    if f_is_active(i_mq_ps)
        f_hline(i_mq_ps, COLOR_GREEN, 2, line.style_solid)
        f_hlabel(i_mq_ps, "MQ Put Sup", color.new(COLOR_GREEN, 80), COLOR_GREEN)
    if f_is_active(i_mq_hvl)
        f_hline(i_mq_hvl, COLOR_YELLOW, 1, line.style_dashed)
        f_hlabel(i_mq_hvl, "MQ HVL", color.new(COLOR_YELLOW, 80), COLOR_YELLOW)
    if f_is_active(i_mq_cr0)
        f_hline(i_mq_cr0, COLOR_RED, 1, line.style_dotted)
        f_hlabel(i_mq_cr0, "CR 0DTE", color.new(COLOR_RED, 75), COLOR_RED)
    if f_is_active(i_mq_ps0)
        f_hline(i_mq_ps0, COLOR_GREEN, 1, line.style_dotted)
        f_hlabel(i_mq_ps0, "PS 0DTE", color.new(COLOR_GREEN, 75), COLOR_GREEN)
    if f_is_active(i_mq_gw0)
        f_hline(i_mq_gw0, COLOR_ORANGE, 1, line.style_dotted)
        f_hlabel(i_mq_gw0, "GW 0DTE", color.new(COLOR_ORANGE, 75), COLOR_ORANGE)
    if f_is_active(i_mq_1dmin)
        f_hline(i_mq_1dmin, COLOR_BLUE_L, 1, line.style_dashed)
        f_hlabel(i_mq_1dmin, "1D Min", color.new(COLOR_BLUE_L, 80), COLOR_BLUE_L)
    if f_is_active(i_mq_1dmax)
        f_hline(i_mq_1dmax, COLOR_BLUE_L, 1, line.style_dashed)
        f_hlabel(i_mq_1dmax, "1D Max", color.new(COLOR_BLUE_L, 80), COLOR_BLUE_L)
    if f_is_active(i_zero_gamma)
        f_hline(i_zero_gamma, COLOR_YELLOW, 2, line.style_dashed)
        f_hlabel(i_zero_gamma, "Zero Gamma / HVL", color.new(COLOR_YELLOW, 75), COLOR_YELLOW)

    f_draw_generic(price, lbl) =>
        if f_is_active(price)
            f_hline(price, COLOR_GRAY, 1, line.style_dotted)
            f_hlabel(price, lbl, color.new(COLOR_GRAY, 70), COLOR_WHITE)

    f_draw_generic(i_gx1p, i_gx1l)
    f_draw_generic(i_gx2p, i_gx2l)
    f_draw_generic(i_gx3p, i_gx3l)
    f_draw_generic(i_gx4p, i_gx4l)
    f_draw_generic(i_gx5p, i_gx5l)
    f_draw_generic(i_qq1p, i_qq1l)
    f_draw_generic(i_qq2p, i_qq2l)
    f_draw_generic(i_qq3p, i_qq3l)

    // BOX PIN 0DTE
    if f_is_active(i_mq_ps0) and f_is_active(i_mq_cr0)
        pin_width = i_mq_cr0 - i_mq_ps0
        pin_class = pin_width < i_pin_narrow ? "📌 STRETTO" :
                    pin_width < i_pin_wide   ? "📌 NORMALE" : "📌 AMPIO"
        bx = box.new(bar_index - 2, i_mq_cr0, bar_index, i_mq_ps0,
                     border_color=COLOR_ORANGE, border_width=1,
                     bgcolor=color.new(COLOR_ORANGE, 88), extend=extend.right)
        array.push(drawn_boxes, bx)
        lb = label.new(bar_index, (i_mq_cr0 + i_mq_ps0) / 2,
                       pin_class + "  " + str.tostring(pin_width, "#.#") + " pt",
                       xloc=xloc.bar_index, yloc=yloc.price,
                       color=color.new(COLOR_ORANGE, 70), textcolor=COLOR_WHITE,
                       style=label.style_label_left, size=size.normal)
        array.push(drawn_labels, lb)

    // BOX AGX ZONES
    n_clusters = array.size(cluster_prices)
    agx_above  = 0
    agx_below  = 0

    for i = 0 to n_clusters - 1
        cp    = array.get(cluster_prices, i)
        cn    = array.get(cluster_counts, i)
        cs    = array.get(cluster_sources, i)
        stars = f_star_rating(cn)
        is_above = cp > close
        if is_above => agx_above += 1 else => agx_below += 1
        zone_col = is_above ? COLOR_RED_T : COLOR_GREEN_T
        bx_top   = cp + i_tol_agx / 2
        bx_bot   = cp - i_tol_agx / 2
        bx = box.new(bar_index - 1, bx_top, bar_index, bx_bot,
                     border_color=is_above ? COLOR_RED : COLOR_GREEN, border_width=1,
                     bgcolor=zone_col, extend=extend.right)
        array.push(drawn_boxes, bx)
        lb = label.new(bar_index + 1, cp, stars + "  " + str.tostring(cp, "#.##") + "\\n" + cs,
                       xloc=xloc.bar_index, yloc=yloc.price,
                       color=is_above ? color.new(COLOR_RED, 70) : color.new(COLOR_GREEN, 70),
                       textcolor=COLOR_WHITE, style=label.style_label_left, size=size.small)
        array.push(drawn_labels, lb)

    // TABELLA RIEPILOGATIVA
    if i_show_table
        tpos = switch i_table_pos
            "Top Right"     => position.top_right
            "Top Left"      => position.top_left
            "Bottom Right"  => position.bottom_right
            "Bottom Left"   => position.bottom_left
            "Top Center"    => position.top_center
            "Bottom Center" => position.bottom_center
            => position.top_right

        tbl = table.new(tpos, 2, 9, bgcolor=color.new(color.black, 60),
                        border_color=COLOR_GRAY, border_width=1,
                        frame_color=COLOR_GRAY, frame_width=1)

        mode_col     = current_mode == "FADE EDGES"      ? COLOR_BG_FADE :
                       current_mode == "FOLLOW MOMENTUM" ? COLOR_BG_FOLLOW : COLOR_BG_TRANS
        mode_txt_col = current_mode == "TRANSIZIONE"     ? color.black : COLOR_WHITE

        table.cell(tbl, 0, 0, "AGX DEALER MECHANICS", bgcolor=color.new(color.black, 40),
                   text_color=COLOR_WHITE, text_size=size.small, text_halign=text.align_center)
        table.merge_cells(tbl, 0, 0, 1, 0)

        table.cell(tbl, 0, 1, "MODE", text_color=COLOR_WHITE, text_size=size.small, bgcolor=color.new(color.black, 60))
        table.cell(tbl, 1, 1, current_mode, text_color=mode_txt_col, bgcolor=mode_col, text_size=size.small, text_halign=text.align_center)

        table.cell(tbl, 0, 2, "Net GEX", text_color=COLOR_WHITE, text_size=size.small, bgcolor=color.new(color.black, 60))
        table.cell(tbl, 1, 2, i_net_gex_pos ? "POSITIVO" : "NEGATIVO",
                   text_color=i_net_gex_pos ? COLOR_GREEN : COLOR_RED, bgcolor=color.new(color.black, 60), text_size=size.small)

        zg_dist = f_is_active(i_zero_gamma) ? close - i_zero_gamma : na
        zg_dist_str = na(zg_dist) ? "n/a" : str.tostring(zg_dist, "+#.#;-#.#") + " pt"
        table.cell(tbl, 0, 3, "Zero Gamma", text_color=COLOR_WHITE, text_size=size.small, bgcolor=color.new(color.black, 60))
        table.cell(tbl, 1, 3, str.tostring(i_zero_gamma) + "  (" + zg_dist_str + ")",
                   text_color=COLOR_YELLOW, bgcolor=color.new(color.black, 60), text_size=size.small)

        table.cell(tbl, 0, 4, "Max Change GEX", text_color=COLOR_WHITE, text_size=size.small, bgcolor=color.new(color.black, 60))
        mcg_col = i_max_change == "Tutto verde (bullish)" ? COLOR_GREEN :
                  i_max_change == "Tutto rosso (bearish)" ? COLOR_RED  : COLOR_YELLOW
        table.cell(tbl, 1, 4, i_max_change, text_color=mcg_col, bgcolor=color.new(color.black, 60), text_size=size.small)

        pin_w_val = f_is_active(i_mq_ps0) and f_is_active(i_mq_cr0) ? i_mq_cr0 - i_mq_ps0 : na
        pin_cls   = na(pin_w_val) ? "n/a" :
                    pin_w_val < i_pin_narrow ? "STRETTO (" + str.tostring(pin_w_val, "#") + " pt)" :
                    pin_w_val < i_pin_wide   ? "NORMALE (" + str.tostring(pin_w_val, "#") + " pt)" :
                                               "AMPIO ("   + str.tostring(pin_w_val, "#") + " pt)"
        table.cell(tbl, 0, 5, "Pin 0DTE", text_color=COLOR_WHITE, text_size=size.small, bgcolor=color.new(color.black, 60))
        table.cell(tbl, 1, 5, pin_cls, text_color=COLOR_ORANGE, bgcolor=color.new(color.black, 60), text_size=size.small)

        table.cell(tbl, 0, 6, "AGX Zones", text_color=COLOR_WHITE, text_size=size.small, bgcolor=color.new(color.black, 60))
        table.cell(tbl, 1, 6, "▲ " + str.tostring(agx_above) + "  ▼ " + str.tostring(agx_below),
                   text_color=COLOR_WHITE, bgcolor=color.new(color.black, 60), text_size=size.small)

        sym_target = f_is_active(i_zero_gamma) ? i_zero_gamma + (i_zero_gamma - close) : na
        sym_str    = na(sym_target) ? "n/a" : str.tostring(sym_target, "#.##")
        table.cell(tbl, 0, 7, "Target Speculare", text_color=COLOR_WHITE, text_size=size.small, bgcolor=color.new(color.black, 60))
        table.cell(tbl, 1, 7, sym_str, text_color=COLOR_BLUE_L, bgcolor=color.new(color.black, 60), text_size=size.small)

        table.cell(tbl, 0, 8, "Spot", text_color=COLOR_WHITE, text_size=size.small, bgcolor=color.new(color.black, 60))
        table.cell(tbl, 1, 8, str.tostring(close, "#.##"), text_color=COLOR_WHITE, bgcolor=color.new(color.black, 60), text_size=size.small)

// ═══════════════════════════════════════════════════════════════════
// ALERT CONDITIONS
// ═══════════════════════════════════════════════════════════════════
n_cl = array.size(cluster_prices)

entered_from_below = false
entered_from_above = false
for i = 0 to n_cl - 1
    cp       = array.get(cluster_prices, i)
    zone_top = cp + i_tol_agx / 2
    zone_bot = cp - i_tol_agx / 2
    if close[1] < zone_bot and close >= zone_bot and close <= zone_top
        entered_from_below := true
    if close[1] > zone_top and close <= zone_top and close >= zone_bot
        entered_from_above := true

alertcondition(entered_from_below,
               title="AGX Zone — Entrata da sotto (LONG bias)",
               message="[AGX] Prezzo entrato in una AGX Zone da sotto — possibile supporto/rimbalzo.")

alertcondition(entered_from_above,
               title="AGX Zone — Entrata da sopra (SHORT bias)",
               message="[AGX] Prezzo entrato in una AGX Zone da sopra — possibile resistenza/rigetto.")

alertcondition(any_flip,
               title="AGX — Flip livello GEXBot",
               message="[AGX] Un nodo GEXBot ha eseguito il flip! Verifica il grafico per il nodo interessato.")

alertcondition(f_is_active(i_mq_cr0) and barstate.isconfirmed and close > i_mq_cr0 + i_buf_flip,
               title="AGX — Rottura Pin 0DTE superiore",
               message="[AGX] Close oltre Call Resistance 0DTE + buffer. Potenziale breakout rialzista del pin.")

alertcondition(f_is_active(i_mq_ps0) and barstate.isconfirmed and close < i_mq_ps0 - i_buf_flip,
               title="AGX — Rottura Pin 0DTE inferiore",
               message="[AGX] Close sotto Put Support 0DTE - buffer. Potenziale breakdown ribassista del pin.")`;

// ── Syntax highlighter minimale per Pine Script ──
function highlightPine(code) {
  const keywords = ['indicator','input','var','if','else','for','to','switch','true','false','na','and','or','not','in','=>',':='];
  const builtins = ['barstate','close','open','high','low','bar_index','dayofmonth','time','color','line','box','label','table','array','str','math','ta','position','extend','xloc','yloc','text','size'];
  const funcs    = ['input\\.bool','input\\.float','input\\.string','array\\.new','array\\.push','array\\.clear','array\\.size','array\\.get','array\\.set','array\\.copy','line\\.new','line\\.delete','box\\.new','box\\.delete','label\\.new','label\\.delete','table\\.new','table\\.cell','table\\.merge_cells','color\\.new','str\\.tostring','f_is_active','f_check_flip','f_star_rating','calc_mode','alertcondition'];

  // Escape HTML
  let out = code.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

  // Comments
  out = out.replace(/(\/\/[^\n]*)/g, '<span class="tk-comment">$1</span>');

  // Strings
  out = out.replace(/"([^"]*)"/g, '<span class="tk-string">"$1"</span>');

  // Numbers
  out = out.replace(/\b(\d+\.?\d*)\b/g, '<span class="tk-number">$1</span>');

  // Builtins (must come before keywords)
  builtins.forEach(b => {
    const re = new RegExp(`\\b(${b})\\b`, 'g');
    out = out.replace(re, '<span class="tk-builtin">$1</span>');
  });

  // Functions
  funcs.forEach(f => {
    const re = new RegExp(`(${f})`, 'g');
    out = out.replace(re, '<span class="tk-func">$1</span>');
  });

  // Keywords
  keywords.forEach(k => {
    const escaped = k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const re = new RegExp(`(?<![\\w.])(${escaped})(?![\\w])`, 'g');
    out = out.replace(re, '<span class="tk-keyword">$1</span>');
  });

  return out;
}

// ── Render codice ──
document.addEventListener('DOMContentLoaded', () => {
  const el = document.getElementById('pine-code');
  if (el) {
    el.innerHTML = highlightPine(PINE_CODE);
  }
});

// ── Copy to clipboard ──
function copyCode() {
  navigator.clipboard.writeText(PINE_CODE).then(() => {
    showToast();
    const btn = document.getElementById('copy-btn');
    if (btn) { btn.textContent = '✓ Copiato!'; setTimeout(() => { btn.textContent = '⎘ Copia'; }, 2000); }
  }).catch(() => {
    // fallback
    const ta = document.createElement('textarea');
    ta.value = PINE_CODE;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    showToast();
  });
}

// ── Download .pine ──
function downloadPine() {
  const blob = new Blob([PINE_CODE], { type: 'text/plain' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = 'agx_dealer_mechanics.pine';
  a.click();
  URL.revokeObjectURL(url);
}

// ── Toast notification ──
function showToast() {
  const t = document.getElementById('toast');
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2800);
}

// ── Smooth navbar links ──
document.querySelectorAll('a[href^="#"]').forEach(a => {
  a.addEventListener('click', e => {
    e.preventDefault();
    const target = document.querySelector(a.getAttribute('href'));
    if (target) target.scrollIntoView({ behavior: 'smooth' });
  });
});
