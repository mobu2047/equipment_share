// 实验设备共享平台 - 前端交互逻辑

(function () {
    // DOM 元素
    const promptEl = document.getElementById('prompt');
    const sendBtn = document.getElementById('sendBtn');
    const replyEl = document.getElementById('reply');
    const locationBtn = document.getElementById('locationBtn');
    const locationInfo = document.getElementById('locationInfo');
    const cardsContainer = document.getElementById('cardsContainer');
    const cardCount = document.getElementById('cardCount');
    
    // 应用状态
    let currentEquipmentList = [];
    let userLocation = null;
    let maxResults = 3; // 默认显示3个推荐结果

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

    // 创建设备卡片HTML
    function createEquipmentCard(item, index) {
        const imageUrl = item.image_url || '/static/assets/placeholder1.svg';
        const tags = (item.tags || []).slice(0, 3); // 最多显示3个标签
        
        return `
            <div class="equipment-card" data-id="${item.id || index}">
                <img src="${imageUrl}" alt="${item.name}" onerror="this.src='/static/assets/placeholder1.svg'">
                <div class="card-body">
                    <h3 class="card-title">${item.name}</h3>
                    <p class="card-desc">${item.description || '暂无描述'}</p>
                    <div class="card-meta">
                        ${item.quantity ? `<span class="meta-item primary">数量: ${item.quantity}</span>` : ''}
                        ${item.address ? `<span class="meta-item">📍 ${item.address}</span>` : ''}
                        ${tags.map(tag => `<span class="meta-item">${tag}</span>`).join('')}
                    </div>
                </div>
            </div>
        `;
    }

    // 更新推荐卡片区域
    function updateEquipmentCards(items) {
        currentEquipmentList = items || [];
        const scrollContainer = cardsContainer.querySelector('.cards-scroll');
        
        // 更新计数
        cardCount.textContent = currentEquipmentList.length;
        
        if (currentEquipmentList.length === 0) {
            scrollContainer.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">🔍</div>
                    <p>暂无匹配的设备推荐</p>
                </div>
            `;
            return;
        }
        
        // 生成卡片HTML
        const cardsHtml = currentEquipmentList
            .map((item, index) => createEquipmentCard(item, index))
            .join('');
        
        scrollContainer.innerHTML = cardsHtml;
        
        // 滚动到顶部
        scrollContainer.scrollTop = 0;
    }

    // 设置按钮加载状态
    function setButtonLoading(button, loading, originalText = '发送查询') {
        if (loading) {
            button.disabled = true;
            // 只在第一次保存原始文本
            if (!button.dataset.originalText) {
                const textSpan = button.querySelector('.btn-text');
                button.dataset.originalText = textSpan ? textSpan.textContent : originalText;
            }
            button.innerHTML = `
                <span class="btn-text">处理中...</span>
                <span class="btn-icon">⏳</span>
            `;
        } else {
            button.disabled = false;
            const text = button.dataset.originalText || originalText;
            button.innerHTML = `
                <span class="btn-text">${text}</span>
                <span class="btn-icon">→</span>
            `;
        }
    }

    // 主查询处理函数
    async function handleSend() {
        const text = (promptEl.value || '').trim();
        if (!text) {
            replyEl.textContent = '请输入实验需求描述';
            return;
        }

        // 设置加载状态
        setButtonLoading(sendBtn, true, '发送查询');
        replyEl.textContent = '🤖 正在分析您的需求，请稍候...';
        updateEquipmentCards([]); // 清空之前的结果

        try {
            // 优先使用 RAG 问答
            const ragResp = await fetch('/api/rag/ask?query=' + encodeURIComponent(text) + '&top_k=' + maxResults, { 
                method: 'POST' 
            });
            
            if (!ragResp.ok) throw new Error(await ragResp.text());
            const rag = await ragResp.json();

            // 显示答案
            let answerText = rag.answer || '';
            let items = (rag && (rag.recommendations || rag.sources)) || [];

            // 如果RAG没有返回设备，尝试简单推荐
            if (!items.length) {
                try {
                    const recommendResp = await fetch('/api/recommend', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ 
                            experiment: text,
                            user_location: userLocation 
                        })
                    });
                    if (recommendResp.ok) {
                        const recommendData = await recommendResp.json();
                        items = (recommendData && recommendData.items) || [];
                    }
                } catch (e) {
                    console.warn('推荐接口调用失败:', e);
                }
            }

            // 显示结果
            replyEl.textContent = answerText || '✅ 查询完成，请查看右侧推荐设备';
            updateEquipmentCards(items);

        } catch (err) {
            console.error('查询失败:', err);
            
            // 回退处理
            try {
                const chat = await postJson('/api/chat', { prompt: text });
                replyEl.textContent = chat.reply || '抱歉，暂时无法处理您的请求';
            } catch (_) {
                replyEl.textContent = '❌ 请求失败: ' + (err.message || '网络连接异常，请稍后重试');
            }
            
            updateEquipmentCards([]);
        } finally {
            setButtonLoading(sendBtn, false, '发送查询');
        }
    }

    // 地理位置相关函数
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

    // 键盘事件处理
    promptEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            handleSend();
        }
    });

    // 初始化
    function init() {
        // 显示初始状态
        updateEquipmentCards([]);
        
        // 绑定事件
        sendBtn.addEventListener('click', handleSend);
        if (locationBtn) {
            locationBtn.addEventListener('click', handleLocationClick);
        }
        
        // 聚焦到输入框
        if (promptEl) {
            promptEl.focus();
        }
    }

    // 页面加载完成后初始化
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();


