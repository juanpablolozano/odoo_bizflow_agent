/** @odoo-module **/

import {
    Component,
    onMounted,
    onPatched,
    onWillStart,
    onWillUnmount,
    proxy,
    signal,
    useProps,
} from "@odoo/owl";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

const SECTION_FIELDS = {
    cover: [
        { key: "title", label: "Title", type: "text" },
        { key: "subtitle", label: "Subtitle", type: "text" },
        { key: "tagline", label: "Tagline", type: "text" },
    ],
    description: [
        { key: "intro", label: "Introduction", type: "textarea" },
        { key: "location", label: "Location", type: "text" },
        { key: "project_type", label: "Project type", type: "text" },
        { key: "problem", label: "Current situation", type: "textarea" },
        { key: "solution", label: "Proposed work", type: "textarea" },
    ],
    before_after: [
        { key: "intro", label: "Introduction", type: "textarea" },
        { key: "notes", label: "Notes", type: "textarea" },
        { key: "after_prompt", label: "After photo notes", type: "textarea" },
    ],
    scope: [
        { key: "intro", label: "Scope overview", type: "textarea" },
    ],
    materials: [
        { key: "intro", label: "Materials intro", type: "textarea" },
        { key: "notes", label: "Notes", type: "textarea" },
    ],
    timeline: [
        { key: "intro", label: "Timeline intro", type: "textarea" },
    ],
    pricing: [
        { key: "intro", label: "Budget intro", type: "textarea" },
        { key: "notes", label: "Notes", type: "textarea" },
        { key: "markup_percent", label: "Markup %", type: "number" },
        { key: "contingency_percent", label: "Contingency %", type: "number" },
    ],
    utility: [
        { key: "intro", label: "Utility intro", type: "textarea" },
        { key: "notes", label: "Notes", type: "textarea" },
    ],
    legal: [
        { key: "terms_md", label: "Terms", type: "textarea" },
        { key: "responsibilities_md", label: "Responsibilities", type: "textarea" },
        { key: "notes", label: "Notes", type: "textarea" },
    ],
    design: [
        { key: "css", label: "Document look (advanced)", type: "textarea" },
    ],
    extras: [],
};

export class ProposalEditorAction extends Component {
    static template = "bizflow_proposals.ProposalEditor";
    props = useProps({
        ...standardActionServiceProps,
    });

    previewRef = signal.ref();

    setup() {
        this.state = proxy({
            list: [],
            proposal: null,
            mode: "list", // list | new | edit
            tab: "sections",
            activeSection: null, // null = section list; key = drill-down
            sectionKey: "cover",
            sectionMode: "chat", // chat | edit | images
            fromPreview: false,
            form: {},
            language: "es",
            clientName: "",
            brief: "",
            workBrief: "",
            projectType: "",
            location: "",
            pendingPhotos: [],
            pendingPlans: [],
            creating: false,
            chatInput: "",
            chatMessages: [],
            chatBusy: false,
            pricingBusy: false,
            imageBusy: false,
            materialQuery: "",
            searchResults: [],
            searchGroups: [],
            searchBusy: false,
            selectedSearchKey: "",
            materialsPrompt: "",
            materialsReportMode: "detailed_prices",
            pricingReview: null,
            ownLineName: "",
            ownLineQty: "1",
            ownLineUom: "unit",
            ownLinePrice: "0",
            utilityIntro: "",
            afterNotes: "",
            designCss: "",
            aiMessage: "",
            pdfMessage: "",
            refreshing: false,
            editingLineId: null,
            editingField: null,
            editingValue: "",
            conflict: null,
            pollTimer: null,
            lastGenerationStatus: null,
            imageLabel: "",
            imageSlot: "before",
            history: [],
            costName: "",
            costRate: "0",
            costQty: "1",
        });
        this._previewWired = false;
        onWillStart(async () => {
            const params = this.props.action?.params || {};
            if (params.proposal_id) {
                await this.openProposal(params.proposal_id);
            } else {
                await this.loadList();
            }
        });
        onMounted(() => this._wirePreview());
        onPatched(() => {
            this._wirePreview();
            this._syncPreviewActive();
        });
        onWillUnmount(() => this._stopPoll());
    }

    get copy() {
        const es = this.state.language === "es" || this.state.proposal?.language === "es";
        if (es) {
            return {
                listTitle: "Propuestas de construcción",
                newProposal: "Nueva propuesta",
                tellAi: "Cuéntale a la IA de qué se trata la obra",
                briefLabel: "¿De qué trata el proyecto?",
                briefPh: "Contexto del cliente, el problema, medidas (ej. cocina 12×14 ft), sistemas, notas de la visita…",
                workLabel: "¿Qué hay que construir?",
                workPh: "Partidas, acabados, restricciones, acceso…",
                createEmpty: "Crear vacía",
                createAi: "Crear y escribir con IA",
                writing: "Escribiendo la propuesta…",
                writingHint: "Tarda 1–2 minutos. Puedes dejar la pestaña abierta; aparece sola al terminar.",
                sections: "Secciones",
                aiAll: "IA · todas",
                images: "Imágenes",
                materials: "Materiales",
                utility: "Utilidad",
                share: "Compartir",
                history: "Historial",
                design: "Diseño",
                chat: "Chat",
                edit: "Editar",
                send: "Enviar",
                chatPh: "Pide un cambio… Enter para enviar",
                upload: "Subir imagen",
                generateAfter: "Generar después (IA)",
                generatingAfter: "Generando imagen después…",
                generatingAfterHint: "La IA está creando la visualización. Puede tardar unos minutos.",
                deleteImage: "Eliminar",
                beforePhotos: "Fotos antes",
                afterForThis: "Después de esta foto",
                noAfterYet: "Aún no hay después",
                coverPhotos: "Cover / otras",
                analyzePricing: "Analizar",
                analyze: "Analizar",
                analyzingPricing: "Analizando materiales y mano de obra…",
                materialsPrompt: "Prompt de materiales",
                materialsPromptPh: "Describe materiales, cantidades y proveedores…",
                linesTitle: "Líneas",
                colDesc: "Descripción",
                colQty: "Cant.",
                colUom: "UoM",
                colUnit: "Unitario",
                colTotal: "Total",
                colSupplier: "Proveedor",
                reportModeLabel: "Cómo se muestra al cliente",
                reportModePrices: "Ítems con precios",
                reportModeNoPrices: "Ítems sin precios (nombre + cant.)",
                reportModeTotal: "Solo total de materiales",
                reviewPricing: "Revisar precios",
                applyPricing: "Agregar todos",
                cancelReview: "Cancelar",
                noVerifiedPrice: "Sin precio en retailer — puedes agregar la cantidad al reporte y editar el precio luego",
                referentialPrice: "Referencial",
                recommended: "Recomendado",
                laborTitle: "Mano de obra",
                laborTrades: "Oficios / mano de obra",
                laborHours: "horas",
                previewTotals: "Total estimado del review",
                addCandidate: "Agregar a líneas",
                skipCandidate: "Descartar",
                allSections: "Todas las secciones",
                cheapest: "más barato",
                addManual: "Agregar material manual",
                addLine: "Agregar",
                searchSuppliers: "Buscar proveedores",
                search: "Buscar",
                searching: "Buscando…",
                noSearchHits: "Sin resultados. Prueba otros términos.",
                openUrl: "Ver producto",
                downloadPdf: "Descargar PDF",
                exportHtml: "Exportar HTML",
                pdfBusy: "Generando PDF…",
                pdfFailed: "No se pudo generar el PDF. Usa imprimir del navegador o instala Chrome.",
                editPriceHint: "Editable siempre — también si vino de IA o proveedor",
                sitePhotos: "Fotos del sitio",
                sitePhotosHint: "Para identificar materiales y el alcance visual (before).",
                planPdf: "Plano / PDF",
                planPdfHint: "Para cantidades, materiales, tiempos y presupuesto desde el plano.",
            };
        }
        return {
            listTitle: "Construction proposals",
            newProposal: "New proposal",
            tellAi: "Tell the AI what the project is about",
            briefLabel: "What is the project about?",
            briefPh: "Client context, the problem, measurements (e.g. kitchen 12×14 ft), systems, site notes…",
            workLabel: "What must be built?",
            workPh: "Trades, finishes, constraints, access…",
            createEmpty: "Create empty",
            createAi: "Create and write with AI",
            writing: "Writing the proposal…",
            writingHint: "It takes 1–2 minutes. Leave this tab open; it appears when done.",
            sections: "Sections",
            aiAll: "AI · all",
            images: "Images",
            materials: "Materials",
            utility: "Utility",
            share: "Share",
            history: "History",
            design: "Design",
            chat: "Chat",
            edit: "Edit",
            send: "Send",
            chatPh: "Ask for a change… Enter to send",
            upload: "Upload image",
            generateAfter: "Generate after (AI)",
            generatingAfter: "Generating after image…",
            generatingAfterHint: "AI is creating the visualization. This can take a few minutes.",
            deleteImage: "Remove",
            beforePhotos: "Before photos",
            afterForThis: "After for this photo",
            noAfterYet: "No after yet",
            coverPhotos: "Cover / other",
            analyzePricing: "Analyze",
            analyze: "Analyze",
            analyzingPricing: "Analyzing materials and labor…",
            materialsPrompt: "Materials prompt",
            materialsPromptPh: "Describe materials, quantities, and suppliers…",
            linesTitle: "Lines",
            colDesc: "Description",
            colQty: "Qty",
            colUom: "UoM",
            colUnit: "Unit",
            colTotal: "Total",
            colSupplier: "Supplier",
            reportModeLabel: "How it shows to the client",
            reportModePrices: "Items with prices",
            reportModeNoPrices: "Items without prices (name + qty)",
            reportModeTotal: "Materials total only",
            reviewPricing: "Review prices",
            applyPricing: "Add all",
            cancelReview: "Cancel",
            noVerifiedPrice: "No retailer price — you can still add the qty to the report and edit price later",
            referentialPrice: "Referential",
            recommended: "Recommended",
            laborTitle: "Labor",
            laborTrades: "Trades / labor",
            laborHours: "hours",
            previewTotals: "Estimated review total",
            addCandidate: "Add to lines",
            skipCandidate: "Dismiss",
            allSections: "All sections",
            cheapest: "cheapest",
            addManual: "Add manual material",
            addLine: "Add",
            searchSuppliers: "Search suppliers",
            search: "Search",
            searching: "Searching…",
            noSearchHits: "No results. Try different terms.",
            openUrl: "Open product",
            downloadPdf: "Download PDF",
            exportHtml: "Export HTML",
            pdfBusy: "Generating PDF…",
            pdfFailed: "PDF export failed. Use browser print or install Chrome.",
            editPriceHint: "Always editable — including AI or provider prices",
            sitePhotos: "Site photos",
            sitePhotosHint: "Used to identify materials and visual scope (before).",
            planPdf: "Plan / PDF",
            planPdfHint: "Used for quantities, materials, timeline, and budget from the drawing.",
        };
    }

    get beforeAssets() {
        return (this.state.proposal?.assets || []).filter((a) => a.slot === "before");
    }

    get otherAssets() {
        const beforeIds = new Set(this.beforeAssets.map((a) => a.id));
        return (this.state.proposal?.assets || []).filter((a) => {
            if (a.slot === "before") {
                return false;
            }
            if (a.slot === "after" && a.parent_id && beforeIds.has(a.parent_id)) {
                return false;
            }
            return true;
        });
    }

    aftersFor(beforeId) {
        return (this.state.proposal?.assets || []).filter(
            (a) => a.slot === "after" && a.parent_id === beforeId
        );
    }

    get sectionEntries() {
        const proposal = this.state.proposal;
        if (!proposal) {
            return [];
        }
        const hidden = new Set(proposal.hidden_sections || []);
        return (proposal.section_order || []).map((key) => ({
            key,
            label: (proposal.section_labels && proposal.section_labels[key]) || key,
            hidden: hidden.has(key),
        }));
    }

    get sectionFields() {
        return SECTION_FIELDS[this.state.sectionKey] || [];
    }

    get activeSectionLabel() {
        const key = this.state.activeSection;
        if (!key) {
            return "";
        }
        const labels = this.state.proposal?.section_labels || {};
        return labels[key] || key;
    }

    get isGenerating() {
        const g = this.state.proposal?.generation;
        return g && g.status === "running";
    }

    get showBusyOverlay() {
        return this.isGenerating || this.state.imageBusy;
    }

    formatMoney(value) {
        const n = Number(value || 0);
        return `$${n.toFixed(2)}`;
    }

    formatQty(value) {
        const n = Number(value || 0);
        if (Number.isInteger(n)) {
            return String(n);
        }
        return String(Math.round(n * 100) / 100);
    }

    onFieldInput(field, ev) {
        this.state[field] = ev.target.value;
    }

    onFormField(key, ev) {
        const value = ev.target.type === "number" ? Number(ev.target.value) : ev.target.value;
        this.state.form = { ...this.state.form, [key]: value };
    }

    async loadList() {
        const data = await this._httpGet("/bizflow/proposals");
        this.state.list = data.items || [];
        this.state.mode = "list";
        this.state.proposal = null;
        this._stopPoll();
    }

    startNew() {
        this.state.mode = "new";
        this.state.proposal = null;
        this.state.brief = "";
        this.state.workBrief = "";
        this.state.clientName = "";
        this.state.projectType = "";
        this.state.location = "";
        this.state.pendingPhotos = [];
        this.state.pendingPlans = [];
        this._stopPoll();
    }

    onPendingPhotos(ev) {
        const files = ev.target.files ? [...ev.target.files] : [];
        if (!files.length) {
            return;
        }
        this.state.pendingPhotos = [...this.state.pendingPhotos, ...files];
        ev.target.value = "";
    }

    onPendingPlans(ev) {
        const files = ev.target.files ? [...ev.target.files] : [];
        if (!files.length) {
            return;
        }
        this.state.pendingPlans = [...this.state.pendingPlans, ...files];
        ev.target.value = "";
    }

    removePendingPhoto(index) {
        this.state.pendingPhotos = this.state.pendingPhotos.filter((_f, i) => i !== index);
    }

    removePendingPlan(index) {
        this.state.pendingPlans = this.state.pendingPlans.filter((_f, i) => i !== index);
    }

    async _uploadPendingAssets(proposalId) {
        const photos = [...(this.state.pendingPhotos || [])];
        const plans = [...(this.state.pendingPlans || [])];
        let lastProposal = null;
        for (const file of photos) {
            const datas = await this._fileToDataUrl(file);
            const result = await rpc(`/bizflow/proposals/${proposalId}/assets`, {
                datas,
                filename: file.name,
                mime: file.type || "image/jpeg",
                label: file.name,
                slot: "before",
            });
            if (result.error) {
                throw new Error(result.message || result.error);
            }
            if (result.proposal) {
                lastProposal = result.proposal;
            }
        }
        for (const file of plans) {
            const datas = await this._fileToDataUrl(file);
            const result = await rpc(`/bizflow/proposals/${proposalId}/assets`, {
                datas,
                filename: file.name,
                mime: file.type || "application/pdf",
                label: file.name,
                slot: "plan",
            });
            if (result.error) {
                throw new Error(result.message || result.error);
            }
            if (result.proposal) {
                lastProposal = result.proposal;
            }
        }
        this.state.pendingPhotos = [];
        this.state.pendingPlans = [];
        return lastProposal;
    }

    async openProposal(id, opts = {}) {
        const data = await this._httpGet(`/bizflow/proposals/${id}`);
        this._applyProposal(data, opts);
        this.state.mode = "edit";
        if (!opts.preferMaterialsReview) {
            this.state.tab = "sections";
            this.state.activeSection = null;
        }
        await this._loadChat("");
        this._startPoll();
    }

    _hydratePricingReviewFromContent(data) {
        const review = data?.content?.pricing?.last_review;
        if (!review || review === false || !Array.isArray(review.candidates)) {
            return false;
        }
        const laborItems = (review.labor_items || []).map((li, i) => ({
            included: true,
            ...li,
            id: li.id || `labor-${i}`,
        }));
        const labor =
            laborItems.length > 0
                ? null
                : review.labor
                  ? { included: true, ...review.labor }
                  : null;
        this.state.pricingReview = {
            candidates: (review.candidates || []).map((c) => this._normalizeCandidate(c)),
            labor,
            labor_items: laborItems,
            exclusions: review.exclusions || [],
            assumptions: review.assumptions || [],
            totals: review.totals || {},
        };
        return (review.candidates || []).length > 0 || laborItems.length > 0 || Boolean(labor);
    }

    _applyProposal(data, opts = {}) {
        this.state.proposal = data;
        this.state.lastGenerationStatus = data.generation?.status || "idle";
        this.state.language = data.language || "es";
        this.state.sectionKey = this.state.sectionKey || "cover";
        this._loadFormFromSection(this.state.sectionKey);
        this.state.utilityIntro = (data.content && data.content.utility && data.content.utility.intro) || "";
        this.state.designCss = (data.content && data.content.design && data.content.design.css) || "";
        this.state.afterNotes = (data.content && data.content.before_after && data.content.before_after.notes) || "";
        const savedPrompt = data.content?.pricing?.materials_prompt || "";
        this.state.materialsPrompt = savedPrompt || this._defaultMaterialsPrompt(data);
        this.state.materialsReportMode = data.materials_report_mode || "detailed_prices";
        if (opts.preferMaterialsReview) {
            const ok = this._hydratePricingReviewFromContent(data);
            if (ok) {
                this.state.tab = "materials";
                this.state.aiMessage = this.copy.reviewPricing;
            }
        }
    }

    _defaultMaterialsPrompt(data) {
        const es = (data?.language || this.state.language) === "es";
        const location = data?.location || "";
        const projectType = data?.project_type || "";
        const brief = data?.brief || data?.description || "";
        const work = data?.work_brief || "";
        if (es) {
            return (
                `Analiza la imagen del sitio (si hay) y el brief para proponer materiales y mano de obra.\n` +
                `Ubicación: ${location}\nTipo: ${projectType}\n` +
                `BRIEF:\n${brief}\n\nWORK:\n${work}`
            );
        }
        return (
            `Analyze the site image (if any) and brief to propose materials and labor.\n` +
            `Location: ${location}\nProject type: ${projectType}\n` +
            `BRIEF:\n${brief}\n\nWORK:\n${work}`
        );
    }

    _loadFormFromSection(key) {
        const section = (this.state.proposal?.content && this.state.proposal.content[key]) || {};
        const form = {};
        for (const field of SECTION_FIELDS[key] || []) {
            form[field.key] = section[field.key] ?? (field.type === "number" ? 0 : "");
        }
        this.state.form = form;
    }

    setTab(tab) {
        this.state.tab = tab;
        if (tab === "materials" && !this.state.pricingReview && this.state.proposal) {
            this._hydratePricingReviewFromContent(this.state.proposal);
        }
        if (tab === "ai") {
            this._loadChat("");
        }
        if (tab === "history") {
            this._loadHistory();
        }
    }

    openSection(key, fromPreview = false) {
        this.state.activeSection = key;
        this.state.sectionKey = key;
        this.state.sectionMode = "chat";
        this.state.fromPreview = !!fromPreview;
        this._loadFormFromSection(key);
        this._loadChat(key);
        if (this.state.proposal) {
            rpc(`/bizflow/proposals/${this.state.proposal.id}/state`, { section: key }).catch(() => {});
        }
        this._syncPreviewActive();
    }

    /** @deprecated use openSection */
    selectSection(key) {
        this.openSection(key, false);
    }

    backToSectionList() {
        this.state.activeSection = null;
        this.state.fromPreview = false;
        this._syncPreviewActive();
    }

    onProductLinkClick() {
        // Modifier .stop keeps radio/label from stealing the click; browser opens target=_blank.
    }

    setSectionMode(mode) {
        this.state.sectionMode = mode;
        if (mode === "chat") {
            this._loadChat(this.state.sectionKey);
        }
    }

    _previewDoc() {
        const iframe = this.previewRef?.();
        try {
            return iframe?.contentDocument || null;
        } catch (_err) {
            return null;
        }
    }

    _ensurePreviewClick(iframe) {
        let doc;
        try {
            doc = iframe?.contentDocument || null;
        } catch (_err) {
            doc = null;
        }
        if (!doc || doc._bfPreviewClick) {
            this._syncPreviewActive();
            return;
        }
        doc._bfPreviewClick = true;
        doc.addEventListener("click", (ev) => {
            const node = ev.target?.closest?.("[data-section]");
            const key = node?.getAttribute("data-section");
            if (!key) {
                return;
            }
            ev.preventDefault();
            this.state.tab = "sections";
            this.openSection(key, true);
        });
        this._syncPreviewActive();
    }

    _wirePreview() {
        const iframe = this.previewRef?.();
        if (!iframe) {
            return;
        }
        // OWL may recreate the iframe node; re-bind load + click for the current element/doc.
        if (this._previewIframeEl !== iframe) {
            this._previewIframeEl = iframe;
            iframe.addEventListener("load", () => this._ensurePreviewClick(iframe));
        }
        this._ensurePreviewClick(iframe);
        setTimeout(() => this._ensurePreviewClick(iframe), 50);
    }

    _syncPreviewActive() {
        const doc = this._previewDoc();
        if (!doc) {
            return;
        }
        doc.querySelectorAll("[data-section].bf-active").forEach((n) => n.classList.remove("bf-active"));
        const key = this.state.activeSection;
        if (!key) {
            return;
        }
        const nodes = doc.querySelectorAll(`[data-section="${key}"]`);
        nodes.forEach((n) => n.classList.add("bf-active"));
        if (nodes.length && !this.state.fromPreview) {
            nodes[0].scrollIntoView({ behavior: "smooth", block: "start" });
        }
        this.state.fromPreview = false;
    }

    async onCreateProposal(withAi) {
        if (this.state.creating) {
            return;
        }
        this.state.creating = true;
        this.state.pricingReview = null;
        this.state.aiMessage = "";
        try {
            const name =
                this.state.clientName.trim() ||
                (this.state.language === "es" ? "Nueva propuesta de obra" : "New construction proposal");
            const hasPending =
                (this.state.pendingPhotos || []).length > 0 || (this.state.pendingPlans || []).length > 0;
            // Create first without AI when files are staged so generation sees photos/plan.
            const data = await rpc("/bizflow/proposals", {
                name,
                brief: this.state.brief,
                description: this.state.brief,
                work_brief: this.state.workBrief,
                project_type: this.state.projectType,
                location: this.state.location,
                language: this.state.language,
                write_with_ai: !!withAi && !hasPending,
            });
            this._applyProposal(data);
            this.state.mode = "edit";
            this.state.tab = "sections";
            if (hasPending && data?.id) {
                const afterUpload = await this._uploadPendingAssets(data.id);
                if (afterUpload) {
                    this._applyProposal(afterUpload);
                } else {
                    await this.openProposal(data.id);
                }
                if (withAi) {
                    await rpc(`/bizflow/proposals/${data.id}/generate`, {
                        message: "Write the full proposal from the description, photos, and plan.",
                    });
                    this._startPoll();
                    await this.onRefresh();
                }
            }
            await this._loadChat("");
            this._startPoll();
        } catch (err) {
            this.state.aiMessage = String(err?.message || err || "Create failed");
        } finally {
            this.state.creating = false;
        }
    }

    async onRefresh(ev) {
        ev?.preventDefault?.();
        ev?.stopPropagation?.();
        if (this.state.refreshing) {
            return;
        }
        if (!this.state.proposal?.id) {
            await this.loadList();
            return;
        }
        // Soft reload: keep tab/section so the click cannot "fall through" onto
        // a remounted control or the preview iframe.
        this.state.refreshing = true;
        const tab = this.state.tab;
        const sectionKey = this.state.sectionKey;
        const sectionMode = this.state.sectionMode;
        const materialsPrompt = this.state.materialsPrompt;
        try {
            const data = await this._httpGet(`/bizflow/proposals/${this.state.proposal.id}`);
            this._applyProposal(data);
            this.state.mode = "edit";
            this.state.tab = tab;
            this.state.sectionKey = sectionKey;
            this.state.sectionMode = sectionMode;
            if (materialsPrompt) {
                this.state.materialsPrompt = materialsPrompt;
            }
            this._startPoll();
        } finally {
            // Defer unlock so the original mouseup cannot hit a new element under the cursor.
            setTimeout(() => {
                this.state.refreshing = false;
            }, 300);
        }
    }

    async onSaveSection() {
        const key = this.state.sectionKey;
        const baseHash = this.state.proposal?.hashes?.[key];
        const result = await rpc(`/bizflow/proposals/${this.state.proposal.id}/sections/${key}`, {
            data: this.state.form,
            base_hash: baseHash,
        });
        if (result.conflict) {
            this.state.conflict = result;
            return;
        }
        this.state.conflict = null;
        this.state.proposal.content[key] = result.section;
        this.state.proposal.html = result.html;
        this.state.proposal.version = result.version;
        if (!this.state.proposal.hashes) {
            this.state.proposal.hashes = {};
        }
        this.state.proposal.hashes[key] = result.hash;
        if (result.amounts) {
            this.state.proposal.amounts = result.amounts;
        }
    }

    async resolveConflict(keepMine) {
        if (!this.state.conflict) {
            return;
        }
        const key = this.state.sectionKey;
        if (keepMine) {
            const result = await rpc(`/bizflow/proposals/${this.state.proposal.id}/sections/${key}`, {
                data: this.state.form,
                base_hash: this.state.conflict.hash,
            });
            if (!result.conflict) {
                this.state.proposal.content[key] = result.section;
                this.state.proposal.html = result.html;
                this.state.proposal.hashes[key] = result.hash;
            }
        } else {
            this.state.form = { ...this.state.conflict.section };
            this.state.proposal.content[key] = this.state.conflict.section;
            this.state.proposal.hashes[key] = this.state.conflict.hash;
        }
        this.state.conflict = null;
    }

    async onToggleHidden() {
        const key = this.state.sectionKey;
        const hidden = new Set(this.state.proposal.hidden_sections || []);
        if (hidden.has(key)) {
            hidden.delete(key);
        } else {
            hidden.add(key);
        }
        const data = await rpc(`/bizflow/proposals/${this.state.proposal.id}/meta`, {
            hidden_sections: [...hidden],
        });
        this._applyProposal(data);
    }

    async onSearchMaterials() {
        const q = (this.state.materialQuery || "").trim();
        if (!q || this.state.searchBusy) {
            return;
        }
        this.state.searchBusy = true;
        this.state.aiMessage = this.copy.searching;
        try {
            const result = await rpc("/bizflow/materials/search", {
                query: q,
                limit: 20,
                location: this.state.proposal?.location || "",
            });
            this.state.searchResults = result.items || [];
            this.state.searchGroups = result.groups || [];
            this.state.selectedSearchKey = "";
            this.state.aiMessage = (result.items || []).length
                ? ""
                : this.copy.noSearchHits;
        } catch (err) {
            this.state.aiMessage = String(err?.message || err || "Search failed");
            this.state.searchResults = [];
            this.state.searchGroups = [];
        } finally {
            this.state.searchBusy = false;
        }
    }

    searchHitKey(hit, index) {
        return `${hit.supplier_id || 0}-${index}-${hit.url || hit.name || ""}`;
    }

    selectSearchHit(key) {
        this.state.selectedSearchKey = key;
    }

    async addMaterialHit(hit) {
        if (this._lineAlreadyHasUrl(hit.url)) {
            this.state.searchResults = (this.state.searchResults || []).filter(
                (h) => (h.url || "") !== (hit.url || "")
            );
            return;
        }
        let materialId = hit.material_id;
        if (!materialId) {
            const imported = await rpc("/bizflow/materials/import", {
                name: hit.name,
                sku: hit.sku,
                price: hit.price,
                url: hit.url,
                supplier_id: hit.supplier_id,
            });
            materialId = imported.id;
        }
        const data = await rpc(`/bizflow/proposals/${this.state.proposal.id}/lines`, {
            name: hit.name,
            line_type: "material",
            quantity: Number(hit.quantity || 1),
            uom_name: hit.uom || "unit",
            price_unit: hit.price || 0,
            cost_unit: hit.price || 0,
            supplier_id: hit.supplier_id || false,
            material_id: materialId || false,
            source: "provider",
            product_url: hit.url || "",
            notes: "",
        });
        this._applyProposal(data);
        const hitUrl = (hit.url || "").trim();
        this.state.searchResults = (this.state.searchResults || []).filter(
            (h) => (h.url || "").trim() !== hitUrl || (!hitUrl && h !== hit)
        );
        this.state.searchGroups = (this.state.searchGroups || [])
            .map((g) => ({
                ...g,
                items: (g.items || []).filter(
                    (h) => (h.url || "").trim() !== hitUrl || (!hitUrl && h !== hit)
                ),
            }))
            .filter((g) => (g.items || []).length);
        if (this.state.selectedSearchKey && this.state.selectedSearchKey.includes(hitUrl)) {
            this.state.selectedSearchKey = "";
        }
        this.state.aiMessage = "";
    }

    async onAddOwnLine() {
        const data = await rpc(`/bizflow/proposals/${this.state.proposal.id}/lines`, {
            name: this.state.ownLineName || "Custom material",
            line_type: "material",
            quantity: Number(this.state.ownLineQty || 1),
            uom_name: this.state.ownLineUom || "unit",
            price_unit: Number(this.state.ownLinePrice || 0),
            cost_unit: Number(this.state.ownLinePrice || 0),
            source: "manual",
            notes: "",
        });
        this._applyProposal(data);
        this.state.ownLineName = "";
        this.state.ownLineQty = "1";
        this.state.ownLineUom = "unit";
        this.state.ownLinePrice = "0";
    }

    async onReportModeChange(ev) {
        const mode = ev.target.value;
        this.state.materialsReportMode = mode;
        const data = await rpc(`/bizflow/proposals/${this.state.proposal.id}/meta`, {
            materials_report_mode: mode,
        });
        this._applyProposal(data);
    }

    lineFieldValue(line, field) {
        if (this.state.editingLineId === line.id && this.state.editingField === field) {
            return this.state.editingValue;
        }
        const raw = line?.[field];
        if (field === "quantity" && raw !== "" && raw !== false && raw !== undefined && raw !== null) {
            return this.formatQty(raw);
        }
        return raw === false || raw === undefined || raw === null ? "" : raw;
    }

    onLineFocus(line, field, ev) {
        this.state.editingLineId = line.id;
        this.state.editingField = field;
        this.state.editingValue = ev.target.value;
    }

    onLineInput(ev) {
        this.state.editingValue = ev.target.value;
    }

    async onUpdateLine(line, field, ev) {
        if (!this.state.proposal || !line?.id) {
            return;
        }
        let value = ev.target.value;
        if (field === "quantity" || field === "price_unit" || field === "cost_unit") {
            value = Number(value || 0);
        }
        // User edits become source=manual so Analizar keeps them; notes stay off the client report.
        const payload = {
            id: line.id,
            name: line.name,
            line_type: line.line_type || "material",
            quantity: line.quantity,
            uom_name: line.uom_name || "unit",
            price_unit: line.price_unit,
            cost_unit: line.cost_unit ?? line.price_unit,
            supplier_id: line.supplier_id || false,
            material_id: line.material_id || false,
            source: "manual",
            product_url: line.product_url || "",
            notes: "",
            [field]: value,
        };
        if (field === "price_unit") {
            payload.cost_unit = value;
        }
        this.state.editingLineId = null;
        this.state.editingField = null;
        this.state.editingValue = "";
        const data = await rpc(`/bizflow/proposals/${this.state.proposal.id}/lines`, payload);
        this._applyProposal(data);
    }

    async removeLine(lineId) {
        const data = await rpc(`/bizflow/proposals/${this.state.proposal.id}/lines/${lineId}`, {
            delete: true,
        });
        this._applyProposal(data);
    }

    async onSyncUtility() {
        const data = await rpc(`/bizflow/proposals/${this.state.proposal.id}/sync_utility`, {});
        this._applyProposal(data);
        this.state.aiMessage = "Utility refreshed from lines";
    }

    async onAnalyzePricing() {
        if (!this.state.proposal || this.state.pricingBusy) {
            return;
        }
        this.state.pricingBusy = true;
        this.state.aiMessage = this.copy.analyzingPricing;
        this.state.pricingReview = null;
        try {
            const result = await rpc(`/bizflow/proposals/${this.state.proposal.id}/ai/pricing`, {
                materials_prompt: this.state.materialsPrompt || "",
                apply: false,
            });
            if (result.error) {
                this.state.aiMessage = result.message || result.error;
                return;
            }
            if (result.preview && result.candidates) {
                const laborItems = (result.labor_items || []).map((li, i) => ({
                    included: true,
                    ...li,
                    id: li.id || `labor-${i}`,
                }));
                const labor =
                    laborItems.length > 0
                        ? null
                        : { included: true, ...(result.labor || {}) };
                this.state.pricingReview = {
                    candidates: (result.candidates || []).map((c) => this._normalizeCandidate(c)),
                    labor,
                    labor_items: laborItems,
                    exclusions: result.exclusions || [],
                    assumptions: result.assumptions || [],
                    totals: result.totals || {},
                };
                this.state.aiMessage = result.message || this.copy.reviewPricing;
                return;
            }
            if (result.proposal) {
                this._applyProposal(result.proposal);
            }
            this.state.aiMessage = result.message || "Pricing updated";
        } catch (err) {
            this.state.aiMessage = String(err?.message || err || "Pricing failed");
        } finally {
            this.state.pricingBusy = false;
        }
    }

    _normalizeCandidate(cand) {
        const options = [...(cand.options || [])].sort(
            (a, b) => Number(a.price || 0) - Number(b.price || 0)
        );
        if (options.length && !options.some((o) => o.recommended)) {
            options[0] = { ...options[0], recommended: true };
        }
        let selectedKey = cand.selected_key || "";
        if (options.length && !options.some((o) => o.key === selectedKey)) {
            selectedKey = (options.find((o) => o.recommended) || options[0]).key;
        }
        return { ...cand, options, selected_key: selectedKey };
    }

    onSelectCandidateOption(candidateId, optionKey) {
        const review = this.state.pricingReview;
        if (!review) {
            return;
        }
        this.state.pricingReview = {
            ...review,
            candidates: (review.candidates || []).map((c) =>
                c.id === candidateId ? { ...c, selected_key: optionKey } : c
            ),
        };
    }

    onToggleLaborIncluded() {
        const review = this.state.pricingReview;
        if (!review?.labor) {
            return;
        }
        this.state.pricingReview = {
            ...review,
            labor: { ...review.labor, included: !review.labor.included },
        };
    }

    cancelPricingReview() {
        this.state.pricingReview = null;
        this.state.aiMessage = "";
    }

    _chosenOption(cand) {
        const options = cand.options || [];
        return (
            options.find((o) => o.key === cand.selected_key) ||
            options.find((o) => o.recommended) ||
            options[0] ||
            null
        );
    }

    _lineAlreadyHasUrl(url) {
        const needle = (url || "").trim();
        if (!needle) {
            return false;
        }
        return (this.state.proposal?.lines || []).some((l) => (l.product_url || "").trim() === needle);
    }

    async addPricingCandidate(cand) {
        if (!this.state.proposal || this.state.pricingBusy) {
            return;
        }
        const chosen = this._chosenOption(cand);
        const name = (chosen?.name || cand.name || cand.query || "").trim();
        if (!name) {
            return;
        }
        const url = (chosen?.url || "").trim();
        const price = Number(chosen?.price || 0);
        const hasVerified =
            Boolean(chosen) && url.startsWith("http") && price > 0;
        // Qty-only / unverified candidates are still addable so the report lists materials.
        if (hasVerified && this._lineAlreadyHasUrl(url)) {
            this._removeCandidateFromReview(cand.id);
            return;
        }
        const isQtyOnly = !hasVerified;
        const isRef = Boolean(
            !isQtyOnly && (chosen?.estimated || chosen?.source === "referential")
        );
        this.state.pricingBusy = true;
        try {
            const data = await rpc(`/bizflow/proposals/${this.state.proposal.id}/lines`, {
                name,
                line_type: "material",
                quantity: Number(cand.quantity || 1),
                uom_name: cand.uom || "unit",
                price_unit: hasVerified ? price : 0,
                cost_unit: hasVerified ? price : 0,
                supplier_id: hasVerified ? chosen.supplier_id || false : false,
                source: isQtyOnly || isRef ? "import" : "provider",
                product_url: hasVerified ? url : "",
                notes: isQtyOnly ? "qty-only" : isRef ? "referential" : "",
            });
            this._applyProposal(data);
            this._removeCandidateFromReview(cand.id);
            this.state.aiMessage = isQtyOnly ? this.copy.noVerifiedPrice : "";
        } catch (err) {
            this.state.aiMessage = String(err?.message || err || "Add failed");
        } finally {
            this.state.pricingBusy = false;
        }
    }

    dismissPricingCandidate(candId) {
        this._removeCandidateFromReview(candId);
    }

    _removeCandidateFromReview(candId) {
        const review = this.state.pricingReview;
        if (!review) {
            return;
        }
        const candidates = (review.candidates || []).filter((c) => c.id !== candId);
        const left = { ...review, candidates };
        this.state.pricingReview =
            candidates.length || (left.labor_items || []).length || left.labor ? left : null;
    }

    get pricingReviewTotals() {
        const review = this.state.pricingReview;
        if (!review) {
            return null;
        }
        if (review.totals && (review.totals.grand || review.totals.materials || review.totals.labor)) {
            return review.totals;
        }
        let materials = 0;
        for (const c of review.candidates || []) {
            const chosen = this._chosenOption(c);
            if (chosen) {
                materials += Number(chosen.price || 0) * Number(c.quantity || 1);
            }
        }
        let labor = 0;
        for (const li of review.labor_items || []) {
            labor += Number(li.total || 0);
        }
        if (review.labor) {
            labor += Number(review.labor.total || 0);
        }
        return { materials, labor, grand: materials + labor };
    }

    async addPricingLabor(laborItem = null) {
        const review = this.state.pricingReview;
        const labor = laborItem || review?.labor;
        if (!this.state.proposal || !labor || this.state.pricingBusy) {
            return;
        }
        this.state.pricingBusy = true;
        try {
            const data = await rpc(`/bizflow/proposals/${this.state.proposal.id}/lines`, {
                name: labor.name || this.copy.laborTitle,
                line_type: "labor",
                quantity: Number(labor.hours || 1),
                uom_name: labor.uom || "hour",
                price_unit: Number(labor.hourly_rate || labor.total || 0),
                cost_unit: Number(labor.hourly_rate || labor.total || 0),
                source: "import",
                product_url: "",
                notes: labor.trade || "",
            });
            this._applyProposal(data);
            if (laborItem?.id) {
                const laborItems = (review.labor_items || []).filter((x) => x.id !== laborItem.id);
                const left = {
                    ...review,
                    labor_items: laborItems,
                    labor: laborItems.length ? null : null,
                    candidates: review.candidates || [],
                };
                this.state.pricingReview =
                    left.candidates.length || (left.labor_items || []).length ? left : null;
            } else {
                const left = {
                    ...review,
                    labor: null,
                    candidates: review.candidates || [],
                };
                this.state.pricingReview =
                    left.candidates.length || (left.labor_items || []).length ? left : null;
            }
            this.state.aiMessage = "";
        } catch (err) {
            this.state.aiMessage = String(err?.message || err || "Add labor failed");
        } finally {
            this.state.pricingBusy = false;
        }
    }

    dismissPricingLabor(laborId = null) {
        const review = this.state.pricingReview;
        if (!review) {
            return;
        }
        let left;
        if (laborId) {
            left = {
                ...review,
                labor_items: (review.labor_items || []).filter((x) => x.id !== laborId),
            };
        } else {
            left = { ...review, labor: null };
        }
        this.state.pricingReview =
            (left.candidates || []).length || (left.labor_items || []).length || left.labor
                ? left
                : null;
    }

    onToggleLaborItemIncluded(laborId) {
        const review = this.state.pricingReview;
        if (!review?.labor_items) {
            return;
        }
        this.state.pricingReview = {
            ...review,
            labor_items: review.labor_items.map((li) =>
                li.id === laborId ? { ...li, included: !li.included } : li
            ),
        };
    }

    async onApplyPricingReview() {
        const review = this.state.pricingReview;
        if (!this.state.proposal || !review || this.state.pricingBusy) {
            return;
        }
        this.state.pricingBusy = true;
        try {
            const result = await rpc(`/bizflow/proposals/${this.state.proposal.id}/ai/pricing`, {
                apply: true,
                materials_prompt: this.state.materialsPrompt || "",
                candidates: review.candidates,
                labor: review.labor,
                labor_items: review.labor_items,
                exclusions: review.exclusions,
            });
            if (result.error) {
                this.state.aiMessage = result.message || result.error;
                return;
            }
            if (result.proposal) {
                this._applyProposal(result.proposal);
            }
            this.state.pricingReview = null;
            this.state.aiMessage = result.message || "Pricing applied";
        } catch (err) {
            this.state.aiMessage = String(err?.message || err || "Apply failed");
        } finally {
            this.state.pricingBusy = false;
        }
    }

    async onSaveUtility() {
        const utility = {
            ...(this.state.proposal.content.utility || {}),
            intro: this.state.utilityIntro,
        };
        const result = await rpc(`/bizflow/proposals/${this.state.proposal.id}/sections/utility`, {
            data: utility,
            base_hash: this.state.proposal.hashes?.utility,
        });
        if (!result.conflict) {
            this.state.proposal.content.utility = result.section;
            this.state.proposal.html = result.html;
        }
    }

    async onGenerateAfter(beforeAssetId) {
        if (!this.state.proposal || this.state.imageBusy || !beforeAssetId) {
            return;
        }
        this.state.imageBusy = true;
        this.state.aiMessage = this.copy.generatingAfter;
        try {
            const result = await rpc(`/bizflow/proposals/${this.state.proposal.id}/ai/before_after`, {
                notes: this.state.afterNotes,
                before_asset_id: beforeAssetId,
            });
            if (result.error) {
                this.state.aiMessage = result.message || result.error;
                return;
            }
            if (result.proposal) {
                this._applyProposal(result.proposal);
            } else if (result.html) {
                this.state.proposal.html = result.html;
            }
            this.state.aiMessage = result.message || this.copy.generateAfter;
        } catch (err) {
            this.state.aiMessage = String(err?.message || err || "Generation failed");
        } finally {
            this.state.imageBusy = false;
        }
    }

    async onDeleteAsset(assetId) {
        if (!this.state.proposal || !assetId || this.state.imageBusy) {
            return;
        }
        this.state.imageBusy = true;
        try {
            const result = await rpc(
                `/bizflow/proposals/${this.state.proposal.id}/assets/${assetId}`,
                { delete: true, force: true }
            );
            if (result.error) {
                this.state.aiMessage = result.message || result.error;
                return;
            }
            if (result.proposal) {
                this._applyProposal(result.proposal);
            }
            this.state.aiMessage = this.state.language === "es" || this.state.proposal?.language === "es"
                ? "Imagen eliminada"
                : "Image removed";
        } catch (err) {
            this.state.aiMessage = String(err?.message || err || "Delete failed");
        } finally {
            this.state.imageBusy = false;
        }
    }

    async onCreateShare() {
        const data = await rpc(`/bizflow/proposals/${this.state.proposal.id}/share`, {
            purpose: "view",
            allow_download: true,
            name: "Client link",
        });
        this._applyProposal(data);
        this.state.tab = "share";
    }

    async onCreateReviewShare() {
        const data = await rpc(`/bizflow/proposals/${this.state.proposal.id}/share`, {
            purpose: "review",
            allow_download: true,
            name: "Review link",
        });
        this._applyProposal(data);
        this.state.tab = "share";
    }

    onExportHtml() {
        window.open(`/bizflow/proposals/${this.state.proposal.id}/export/html`, "_blank");
    }

    onExportPdf(ev) {
        ev?.preventDefault?.();
        ev?.stopPropagation?.();
        if (!this.state.proposal?.id) {
            return;
        }
        // Same as HTML export: new tab + Content-Disposition attachment.
        // Avoids synthetic <a>.click() which can hit the current UI after re-render.
        this.state.pdfMessage = "";
        window.open(`/bizflow/proposals/${this.state.proposal.id}/export/pdf`, "_blank", "noopener");
    }

    async onCreateSigning() {
        const result = await rpc(`/bizflow/proposals/${this.state.proposal.id}/signing/requests`, {});
        await this.onRefresh();
        this.state.tab = "sign";
        if (result.url) {
            this.state.aiMessage = result.url;
        }
    }

    async onCompanySign(requestId) {
        const data = await rpc(
            `/bizflow/proposals/${this.state.proposal.id}/signing/${requestId}/company`,
            {}
        );
        this._applyProposal(data);
    }

    async onAddCost() {
        const data = await rpc(`/bizflow/proposals/${this.state.proposal.id}/costs`, {
            name: this.state.costName || "Labor",
            category: "labor",
            quantity: Number(this.state.costQty || 1),
            rate: Number(this.state.costRate || 0),
        });
        this._applyProposal(data);
        this.state.costName = "";
        this.state.costRate = "0";
        this.state.costQty = "1";
    }

    async onDeleteCost(id) {
        const data = await rpc(`/bizflow/proposals/${this.state.proposal.id}/costs`, {
            id,
            delete: true,
        });
        this._applyProposal(data);
    }

    async onRotateCode() {
        const result = await rpc(`/bizflow/proposals/${this.state.proposal.id}/access_code`, {
            rotate: true,
        });
        this.state.proposal.access_code = result.access_code;
    }

    async onToggleAccessRequired() {
        const result = await rpc(`/bizflow/proposals/${this.state.proposal.id}/access_code`, {
            access_required: !this.state.proposal.access_required,
        });
        this.state.proposal.access_required = result.access_required;
        this.state.proposal.access_code = result.access_code;
    }

    async onSaveDesign() {
        const design = {
            ...(this.state.proposal.content.design || {}),
            css: this.state.designCss,
        };
        const result = await rpc(`/bizflow/proposals/${this.state.proposal.id}/sections/design`, {
            data: design,
        });
        this.state.proposal.content.design = result.section;
        this.state.proposal.html = result.html;
    }

    async onGenerateNow() {
        await rpc(`/bizflow/proposals/${this.state.proposal.id}/generate`, {
            message: "Write the full proposal from the description.",
        });
        this._startPoll();
        await this.onRefresh();
    }

    async _loadChat(thread) {
        if (!this.state.proposal) {
            return;
        }
        const data = await this._httpGet(
            `/bizflow/proposals/${this.state.proposal.id}/chat?thread=${encodeURIComponent(thread || "")}`
        );
        const msgs = data.messages || [];
        let start = 0;
        for (let i = msgs.length - 1; i >= 0; i--) {
            if (msgs[i].role === "reset") {
                start = i + 1;
                break;
            }
        }
        this.state.chatMessages = msgs.slice(start).filter((m) => m.role !== "reset");
    }

    async sendChat() {
        const text = (this.state.chatInput || "").trim();
        if (!text || this.state.chatBusy) {
            return;
        }
        this.state.chatBusy = true;
        try {
            const isPage = this.state.tab === "sections" && this.state.sectionMode === "chat";
            let data;
            if (isPage) {
                data = await rpc(
                    `/bizflow/proposals/${this.state.proposal.id}/pages/${this.state.sectionKey}/chat`,
                    { message: text }
                );
            } else {
                data = await rpc(`/bizflow/proposals/${this.state.proposal.id}/chat`, {
                    message: text,
                    thread: "",
                });
            }
            this.state.chatInput = "";
            this._applyProposal(data);
            await this._loadChat(isPage ? this.state.sectionKey : "");
        } finally {
            this.state.chatBusy = false;
        }
    }

    onChatKeydown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.sendChat();
        }
    }

    async resetChat() {
        const thread =
            this.state.tab === "sections" && this.state.sectionMode === "chat"
                ? this.state.sectionKey
                : "";
        await rpc(`/bizflow/proposals/${this.state.proposal.id}/chat/reset`, { thread });
        await this._loadChat(thread);
    }

    async onPickImage(ev) {
        const files = ev.target.files ? [...ev.target.files] : [];
        if (!files.length || !this.state.proposal) {
            return;
        }
        this.state.imageBusy = true;
        try {
            let lastProposal = null;
            for (const file of files) {
                const datas = await this._fileToDataUrl(file);
                const isPdf =
                    (file.type || "").includes("pdf") ||
                    String(file.name || "")
                        .toLowerCase()
                        .endsWith(".pdf");
                const slot = isPdf ? "plan" : this.state.imageSlot || "before";
                const result = await rpc(`/bizflow/proposals/${this.state.proposal.id}/assets`, {
                    datas,
                    filename: file.name,
                    mime: file.type || (isPdf ? "application/pdf" : "image/png"),
                    label: this.state.imageLabel || file.name,
                    slot,
                });
                if (result.error) {
                    this.state.aiMessage = result.message || result.error;
                    break;
                }
                if (result.proposal) {
                    lastProposal = result.proposal;
                }
            }
            if (lastProposal) {
                this._applyProposal(lastProposal);
            }
            this.state.aiMessage =
                this.state.language === "es" || this.state.proposal?.language === "es"
                    ? "Imagen(es) subida(s)"
                    : "Image(s) uploaded";
        } catch (err) {
            this.state.aiMessage = String(err?.message || err || "Upload failed");
        } finally {
            this.state.imageBusy = false;
            ev.target.value = "";
        }
    }

    _fileToDataUrl(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
    }

    async _loadHistory() {
        if (!this.state.proposal) {
            return;
        }
        const data = await this._httpGet(`/bizflow/proposals/${this.state.proposal.id}/changes`);
        this.state.history = data.items || [];
    }

    get historyItems() {
        return this.state.history || [];
    }

    async restoreChange(id) {
        const data = await rpc(`/bizflow/proposals/${this.state.proposal.id}/changes/${id}/restore`, {
            to: "before",
        });
        this._applyProposal(data);
        await this._loadHistory();
    }

    _startPoll() {
        this._stopPoll();
        this.state.pollTimer = setInterval(() => this._poll(), 2000);
    }

    _stopPoll() {
        if (this.state.pollTimer) {
            clearInterval(this.state.pollTimer);
            this.state.pollTimer = null;
        }
    }

    async _poll() {
        if (!this.state.proposal) {
            return;
        }
        try {
            const state = await this._httpGet(`/bizflow/proposals/${this.state.proposal.id}/state`);
            const prevStatus = this.state.lastGenerationStatus;
            const nextStatus = state.generation?.status || "idle";
            const proposal = this.state.proposal;
            if (!proposal) {
                return;
            }
            // Only touch reactive fields when values actually change — avoids
            // re-rendering the preview iframe on every 2s poll tick.
            const genChanged =
                JSON.stringify(proposal.generation || {}) !== JSON.stringify(state.generation || {});
            if (genChanged) {
                proposal.generation = state.generation;
            }
            if (proposal.version !== state.version) {
                proposal.version = state.version;
            }
            if (JSON.stringify(proposal.hashes || {}) !== JSON.stringify(state.hashes || {})) {
                proposal.hashes = state.hashes;
            }
            this.state.lastGenerationStatus = nextStatus;
            // Reload once when generation finishes — not on every poll while status stays "done".
            if (nextStatus === "done" && prevStatus === "running") {
                // 2A: open Materials with persisted last_review (no auto-apply).
                await this.openProposal(proposal.id, { preferMaterialsReview: true });
                if (!this.state.pricingReview) {
                    this.state.tab = "materials";
                    await this.onAnalyzePricing();
                }
            } else if (nextStatus === "error" && prevStatus === "running") {
                this.state.aiMessage = state.generation?.error || "Generation failed";
            }
        } catch {
            // ignore transient poll errors
        }
    }

    async _httpGet(url) {
        const response = await fetch(url, { credentials: "same-origin" });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        return response.json();
    }
}

registry.category("actions").add("bizflow_proposal_editor", ProposalEditorAction);
