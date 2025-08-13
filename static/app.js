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
            // 1) 对话回复
            const chat = await postJson('/api/chat', { prompt: text });
            replyEl.textContent = chat.reply || '';

            // 2) 设备推荐
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
                } else {
                    title.textContent = `设备位 ${i + 1}`;
                    desc.textContent = '等待推荐…';
                }
            }
        } catch (err) {
            replyEl.textContent = '请求失败：' + (err && err.message ? err.message : '未知错误');
        }
    }

    sendBtn.addEventListener('click', handleSend);
})();


