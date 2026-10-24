import streamlit as st
import plotly.graph_objects as go

from data_prep import load_statement, get_company, to_daily, data_summary
from categorizer_ml import categorize
from regular_payments import find_regular
from forecaster import forecast_flows
from cashflow_engine import build_forecast, find_gaps
from categories import TRANSFERS

st.set_page_config(page_title='Прогноз денег', layout='wide')

# пока прогноз и поиск разрывов - заглушки, не показываем их
FORECAST_READY = False


@st.cache_data
def load_dataset():
    return load_statement('data/all_operations.csv')


st.sidebar.title('Данные')
source = st.sidebar.radio('Откуда взять выписку', ['Пример из датасета', 'Загрузить свой CSV'])

if source == 'Загрузить свой CSV':
    file = st.sidebar.file_uploader('Выписка (CSV, разделитель ;)', type='csv')
    if file is None:
        st.info('Загрузите выписку слева')
        st.stop()
    try:
        ops, problems = load_statement(file)
    except ValueError as e:
        st.error(str(e))
        st.stop()
else:
    ops, problems = load_dataset()

company_id = st.sidebar.selectbox('Компания', sorted(ops['company_id'].unique()))
ops = get_company(ops, company_id)
daily = to_daily(ops)
summary = data_summary(ops, daily)

ops = categorize(ops)
events = find_regular(ops)
flows = forecast_flows(daily, ops, events)
forecast = build_forecast(summary['last_balance'], summary['last_date'], flows, events)
alerts = find_gaps(forecast, events)

st.title('Прогноз остатка на 30 дней')

col1, col2, col3 = st.columns(3)
col1.metric('Остаток сейчас', f"{summary['last_balance']:,.0f} ₽".replace(',', ' '))
col2.metric('История', f"{summary['days']} дн.")
col3.metric('Операций', summary['operations'])

if summary['short_history']:
    st.warning('История меньше 3 месяцев, прогноз будет неточным')
if summary['gaps'] > 0:
    st.warning(f"В выписке есть пропуски: {summary['gaps']} шт., всего {summary['gap_days']} дн.")

if FORECAST_READY:
    if alerts:
        for alert in alerts:
            st.error(f"{alert['date']:%d.%m.%Y}: может не хватить {alert['shortfall']:,.0f} ₽")
    else:
        st.success('Кассовых разрывов в ближайшие 30 дней не видно')

fig = go.Figure()
fig.add_trace(go.Scatter(x=daily['date'], y=daily['balance'], name='История'))
if FORECAST_READY:
    fig.add_trace(go.Scatter(x=forecast['date'], y=forecast['balance'], name='Прогноз',
                             line=dict(dash='dash')))
fig.add_hline(y=0, line_color='red', line_dash='dot')
fig.update_layout(height=450, yaxis_title='₽')
st.plotly_chart(fig, width='stretch')

# ---------- категории ----------
st.header('Куда приходят и уходят деньги')

rules_share = (ops['category_source'] == 'rules').mean() * 100
st.caption(f'Категория определена для {rules_share:.1f}% операций')


def category_chart(part, title):
    by_cat = part.groupby('category')['amount'].sum().abs().sort_values()
    fig = go.Figure(go.Bar(x=by_cat.values, y=by_cat.index, orientation='h'))
    fig.update_layout(title=title, height=450, xaxis_title='₽', margin=dict(l=10))
    return fig


# переводы между своими счетами - это не доход и не расход, на графиках их не показываем
real = ops[ops['category'] != TRANSFERS]
col_in, col_out = st.columns(2)
col_in.plotly_chart(category_chart(real[real['amount'] > 0], 'Поступления'), width='stretch')
col_out.plotly_chart(category_chart(real[real['amount'] < 0], 'Списания'), width='stretch')
st.caption('Переводы между своими счетами в другие банки не показаны: это не доход и не расход')

with st.expander('Операции по категориям'):
    chosen = st.selectbox('Категория', ['Все'] + sorted(ops['category'].unique()))
    table = ops if chosen == 'Все' else ops[ops['category'] == chosen]
    st.dataframe(table[['date', 'amount', 'counterparty', 'purpose', 'category']],
                 width='stretch', hide_index=True)

with st.expander('Проблемы в данных'):
    for p in problems:
        st.write('-', p)
