// 前端逻辑：向后端发送对话与推荐请求，并更新页面

(function () {
    // 为什么自执行函数：
    // - 避免全局变量污染
    // - 在 DOMReady 后立即绑定事件

    const promptEl = document.getElementById('prompt');
    const sendBtn = document.getElementById('sendBtn');
    const replyEl = document.getElementById('reply');

    // 卡片位：用于填充后端的推荐结果
    const cards = [
        document.getElementById('card1'),
        document.getElementById('card2'),
        document.getElementById('card3'),
    ];

    async function postJson(url, data) {
        // 封装 fetch，统一 headers 与错误处理
        const resp = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!resp.ok) {
            const text = await resp.text();
            throw new Error(text);
        }
        return resp.json();
    }

    async function handleSend() {
        const text = (promptEl.value || '').trim();
        if (!text) {
            replyEl.textContent = '请输入内容';
            return;
        }

        replyEl.textContent = '思考中…';

        try {
            // 优先使用 RAG 问答，确保回答包含设备信息
            const ragResp = await fetch('/api/rag/ask?query=' + encodeURIComponent(text) + '&top_k=3', { method: 'POST' });
            if (!ragResp.ok) throw new Error(await ragResp.text());
            const rag = await ragResp.json();
            // 主回答文本
            let answerText = rag.answer || '';

            let items = (rag && (rag.recommendations || rag.sources)) || [];
            // 再请求一次结构化检索结果，用于卡片（避免 ask 未带 image_url 的情况）
            try {
                const searchResp = await fetch('/api/rag/search?query=' + encodeURIComponent(text) + '&top_k=3');
                if (searchResp.ok) {
                    const searchData = await searchResp.json();
                    if (searchData && Array.isArray(searchData.items) && searchData.items.length) {
                        items = searchData.items;
                    }
                }
            } catch (_) { /* ignore */ }
            // 在回答下方追加完整设备明细（名称/标签/图片URL）
            if (items.length) {
                const lines = ['','设备详情：'];
                for (let i = 0; i < items.length; i++) {
                    const it = items[i];
                    lines.push(`- [${i + 1}] 名称: ${it.name} | 标签: ${(it.tags || []).join(', ')} | 图片: ${it.image_url || '-'}`);
                }
                answerText = (answerText ? answerText + '\n\n' : '') + lines.join('\n');
            }
            replyEl.textContent = answerText;
            for (let i = 0; i < cards.length; i++) {
                const card = cards[i];
                const item = items[i];
                const img = card.querySelector('img');
                const title = card.querySelector('.card-title');
                const desc = card.querySelector('.card-desc');
                if (item) {
                    // 如果后端提供图片，优先展示
                    if (item.image_url) {
                        // 加时间戳避免缓存
                        img.src = item.image_url + (item.image_url.includes('?') ? '&' : '?') + 't=' + Date.now();
                    }
                    title.textContent = item.name || `设备位 ${i + 1}`;
                    desc.textContent = (item.description || '') + (item.tags ? ` [${item.tags.join(', ')}]` : '');
                } else {
                    title.textContent = `设备位 ${i + 1}`;
                    desc.textContent = '等待推荐…';
                }
            }
        } catch (err) {
            // 回退：使用原有对话+简单推荐
            try {
                const chat = await postJson('/api/chat', { prompt: text });
                replyEl.textContent = chat.reply || '';
            } catch (_) { /* 忽略 */ }
            try {
                const rec = await postJson('/api/recommend', { experiment: text });
                const items = (rec && rec.items) || [];
                for (let i = 0; i < cards.length; i++) {
                    const card = cards[i];
                    const item = items[i];
                    const img = card.querySelector('img');
                    const title = card.querySelector('.card-title');
                    const desc = card.querySelector('.card-desc');
                    if (item) {
                        img.src = item.image_url || img.src;
                        title.textContent = item.name || `设备位 ${i + 1}`;
                        desc.textContent = (item.description || '') + (item.tags ? ` [${item.tags.join(', ')}]` : '');
                    }
                }
            } catch (_) { /* 忽略 */ }
            if (replyEl.textContent === '思考中…') {
                replyEl.textContent = '请求失败：' + (err && err.message ? err.message : '未知错误');
            }
        }
    }

    sendBtn.addEventListener('click', handleSend);
})();


