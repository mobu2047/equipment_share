// 前端逻辑：向后端发送对话与推荐请求，并更新页面

(function () {
    // 为什么自执行函数：
    // - 避免全局变量污染
    // - 在 DOMReady 后立即绑定事件

    const promptEl = document.getElementById('prompt');
    const sendBtn = document.getElementById('sendBtn');
    const replyEl = document.getElementById('reply');
    const locationBtn = document.getElementById('locationBtn');
    const locationInfo = document.getElementById('locationInfo');



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
            // 强制优先使用 RAG 问答
            const ragResp = await fetch('/api/rag/ask?query=' + encodeURIComponent(text) + '&top_k=3', { method: 'POST' });
            if (!ragResp.ok) throw new Error(await ragResp.text());
            const rag = await ragResp.json();

            // 1) 展示答案
            let answerText = rag.answer || '';

            // 2) 设备列表（优先用 ask 的 recommendations/sources）
            let items = (rag && (rag.recommendations || rag.sources)) || [];

            // 如 RAG 未返回设备，则回退一次简单推荐以填充卡片
            if (!items.length) {
                try {
                    const recommendResp = await fetch('/api/recommend', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ experiment: text })
                    });
                    if (recommendResp.ok) {
                        const recommendData = await recommendResp.json();
                        items = (recommendData && recommendData.items) || [];
                    }
                } catch (_) { /* ignore */ }
            }

            // 3) 在答案下方追加设备详情
            if (items.length) {
                const lines = ['','设备详情：'];
                for (let i = 0; i < items.length; i++) {
                    const it = items[i];
                    let itemInfo = `- [${i + 1}] 名称: ${it.name}`;
                    if (typeof it.quantity === 'number') {
                        itemInfo += ` | 台数: ${it.quantity}`;
                    }
                    if (typeof it.lat === 'number' && typeof it.lng === 'number') {
                        itemInfo += ` (${it.lat.toFixed(6)}, ${it.lng.toFixed(6)})`;
                    }
                    if (it.address) {
                        itemInfo += ` | 地址: ${it.address}`;
                    }
                    if (it.tags && it.tags.length) {
                        itemInfo += ` | 标签: ${it.tags.join(', ')}`;
                    }
                    lines.push(itemInfo);
                }
                answerText = (answerText ? answerText + '\n\n' : '') + lines.join('\n');
            }

            replyEl.textContent = answerText || '未找到相关设备推荐';

            // 4) 更新卡片
            for (let i = 0; i < cards.length; i++) {
                const card = cards[i];
                const item = items[i];
                const img = card.querySelector('img');
                const title = card.querySelector('.card-title');
                const desc = card.querySelector('.card-desc');
                if (item) {
                    if (item.image_url) {
                        img.src = item.image_url + (item.image_url.includes('?') ? '&' : '?') + 't=' + Date.now();
                    }
                    title.textContent = item.name || `设备位 ${i + 1}`;
                    let descText = item.description || '';
                    if (typeof item.quantity === 'number') descText += `\n🧰 台数: ${item.quantity}`;
                    if (item.address) descText += `\n📍 ${item.address}`;
                    if (typeof item.lat === 'number' && typeof item.lng === 'number') {
                        descText += ` (${item.lat.toFixed(6)}, ${item.lng.toFixed(6)})`;
                    }
                    if (item.tags) descText += ` [${item.tags.join(', ')}]`;
                    desc.textContent = descText;
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
                        desc.textContent = (item.description || '') + (item.tags ? ` [${item.tags.join(', ')}` : '');
                    }
                }
            } catch (_) { /* 忽略 */ }
            if (replyEl.textContent === '思考中…') {
                replyEl.textContent = '请求失败：' + (err && err.message ? err.message : '未知错误');
            }
        }
    }

    // 用户位置相关
    let userLocation = null;

    async function getUserLocation() {
        return new Promise((resolve, reject) => {
            if (!navigator.geolocation) {
                reject(new Error('浏览器不支持地理定位'));
                return;
            }
            
            locationBtn.textContent = '定位中...';
            locationBtn.disabled = true;
            
            navigator.geolocation.getCurrentPosition(
                position => {
                    const location = {
                        lat: position.coords.latitude,
                        lng: position.coords.longitude,
                        accuracy: position.coords.accuracy
                    };
                    resolve(location);
                },
                error => {
                    let errorMsg = '定位失败';
                    switch(error.code) {
                        case error.PERMISSION_DENIED:
                            errorMsg = '用户拒绝了定位请求';
                            break;
                        case error.POSITION_UNAVAILABLE:
                            errorMsg = '位置信息不可用';
                            break;
                        case error.TIMEOUT:
                            errorMsg = '定位请求超时';
                            break;
                    }
                    reject(new Error(errorMsg));
                },
                {
                    enableHighAccuracy: true,
                    timeout: 10000,
                    maximumAge: 300000 // 5分钟缓存
                }
            );
        });
    }

    async function handleLocationClick() {
        try {
            locationInfo.textContent = '正在获取位置...';
            locationInfo.className = 'location-info getting';
            
            const location = await getUserLocation();
            userLocation = location;
            
            // 显示位置信息
            locationInfo.innerHTML = `
                📍 位置已获取<br>
                <small>纬度: ${location.lat.toFixed(6)}, 经度: ${location.lng.toFixed(6)}</small><br>
                <small>精度: ±${Math.round(location.accuracy)}米</small>
            `;
            locationInfo.className = 'location-info success';
            
            locationBtn.textContent = '重新定位';
            locationBtn.disabled = false;
            
            // 可选：调用地理编码API获取地址
            try {
                const address = await reverseGeocode(location.lat, location.lng);
                if (address) {
                    locationInfo.innerHTML = `
                        📍 ${address}<br>
                        <small>纬度: ${location.lat.toFixed(6)}, 经度: ${location.lng.toFixed(6)}</small>
                    `;
                }
            } catch (e) {
                // 地址解析失败不影响主功能
                console.log('地址解析失败:', e);
            }
            
        } catch (error) {
            locationInfo.textContent = `❌ ${error.message}`;
            locationInfo.className = 'location-info error';
            locationBtn.textContent = '获取位置';
            locationBtn.disabled = false;
        }
    }

    async function reverseGeocode(lat, lng) {
        // 简单的地理编码（可以替换为更精确的服务）
        try {
            const response = await fetch(`https://api.bigdatacloud.net/data/reverse-geocode-client?latitude=${lat}&longitude=${lng}&localityLanguage=zh`);
            const data = await response.json();
            return data.city && data.locality ? `${data.city}${data.locality}` : `${data.city || ''}${data.principalSubdivision || ''}`;
        } catch (e) {
            return null;
        }
    }

    // 事件绑定
    sendBtn.addEventListener('click', handleSend);
    if (locationBtn) {
        locationBtn.addEventListener('click', handleLocationClick);
    }
})();


