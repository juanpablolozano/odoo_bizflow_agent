FROM odoo:20

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium \
        poppler-utils \
        fonts-liberation \
        curl \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-addons.txt /tmp/requirements-addons.txt
RUN pip3 install --break-system-packages --no-cache-dir -r /tmp/requirements-addons.txt \
    && rm -f /tmp/requirements-addons.txt

COPY custom_addons/ /mnt/extra-addons/
RUN chown -R odoo:odoo /mnt/extra-addons

COPY docker/odoo.conf /etc/odoo/odoo.conf
COPY docker/entrypoint.sh /bizflow-entrypoint.sh
RUN chmod +x /bizflow-entrypoint.sh \
    && chown odoo:odoo /etc/odoo/odoo.conf

ENV CHROME_BIN=/usr/bin/chromium
ENV ODOO_MODULES=bizflow_proposals
ENV ODOO_DB=odoo

USER odoo

ENTRYPOINT ["/bizflow-entrypoint.sh"]
CMD ["odoo"]
