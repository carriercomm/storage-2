/*
 Created as part of the StratusLab project (http://stratuslab.eu),
 co-funded by the European Commission under the Grant Agreement
 INSFO-RI-261552.

 Copyright (c) 2011, Centre National de la Recherche Scientifique (CNRS)

 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at

 http://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
 */
package eu.stratuslab.storage.disk.resources;

import static org.restlet.data.MediaType.APPLICATION_JSON;
import static org.restlet.data.MediaType.TEXT_HTML;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.util.HashMap;
import java.util.LinkedList;
import java.util.List;
import java.util.Map;
import java.util.zip.GZIPInputStream;

import org.apache.commons.fileupload.FileItem;
import org.apache.commons.fileupload.FileUploadException;
import org.apache.commons.fileupload.disk.DiskFileItemFactory;
import org.restlet.data.Form;
import org.restlet.data.Status;
import org.restlet.ext.fileupload.RestletFileUpload;
import org.restlet.representation.Representation;
import org.restlet.resource.Get;
import org.restlet.resource.ResourceException;

import eu.stratuslab.storage.disk.main.ServiceConfiguration;
import eu.stratuslab.storage.disk.utils.DiskUtils;
import eu.stratuslab.storage.disk.utils.FileUtils;
import eu.stratuslab.storage.persistence.Disk;
import eu.stratuslab.storage.persistence.DiskView;
import eu.stratuslab.storage.persistence.Instance;

public class InstancesResource extends DiskBaseResource {

	private Form form = null;
	
	@Get("html")
	public Representation getAsHtml() {

		Map<String, Object> info = listInstances();

		return createTemplateRepresentation("html/instances.ftl", info, TEXT_HTML);
	}

	@Get("json")
	public Representation getAsJson() {

		Map<String, Object> info = listInstances();

		return createTemplateRepresentation("json/instances.ftl", info,
				APPLICATION_JSON);

	}

	private Map<String, Object> listInstances() {
		Map<String, Object> info = createInfoStructure("Instances list");

		addCreateFormDefaults(info);

		String username = getUsername(getRequest());
//		List<InstancesView> disks;
//		if(isSuperUser(username)){
//			disks = Instance.listAll();
//		} else {
//			disks = Instance.listAllByUser(username);			
//		}
//		info.put("instances", instances);

		return info;
	}

	private void addCreateFormDefaults(Map<String, Object> info) {
		Map<String, Object> defaults = new HashMap<String, Object>();
		defaults.put(Disk.DISK_SIZE_KEY, 1);
		defaults.put(Disk.DISK_VISIBILITY_KEY,
				DiskVisibility.PRIVATE.toString());

		info.put("values", defaults);

		List<String> visibilities = new LinkedList<String>();
		for (DiskVisibility visibility : DiskVisibility.values()) {
			visibilities.add(visibility.toString());
		}

		info.put("visibilities", visibilities);
	}

}